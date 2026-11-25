"""
Peeppo -- «Дурак» (подкидной, 1 на 1 против компьютера) на гемы.

Вся логика и ИИ на сервере: клиент только присылает свой ход (карта / беру / бито /
сдаюсь), рука бота и колода клиенту не отдаются до конца партии.

Подключение в api.py (одна строка в самом конце файла):
    import durak as _durak; _durak.register(app, _authenticate)

Ставка списывается на старте. Победа платит bet * DURAK_WIN_MULT, ничья (оба вышли
одновременно) возвращает ставку, поражение / «сдаться» -- 0.
"""

import json
import random
from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import BaseModel

import database as db

DURAK_MIN_BET = 1
DURAK_MAX_BET = 1_000_000
DURAK_WIN_MULT = 1.9

RANKS = "6789TJQKA"
SUITS = "SHDC"
RANK_LABEL = {"T": "10"}
SUIT_SYM = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS durak_games (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(telegram_id),
    bet          INTEGER NOT NULL,
    state        TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active',  -- active | won | lost | draw
    payout       INTEGER,
    ver          INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    resolved_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_durak_user_status ON durak_games(user_id, status);
"""


class DurakError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_table() -> None:
    with db.get_conn() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Card helpers
# ---------------------------------------------------------------------------

def rv(card: str) -> int:
    return RANKS.index(card[0])


def nice(card: str) -> str:
    return RANK_LABEL.get(card[0], card[0]) + SUIT_SYM[card[1]]


def beats(d: str, a: str, ts: str) -> bool:
    if d[1] == a[1]:
        return rv(d) > rv(a)
    return d[1] == ts and a[1] != ts


def cost(card: str, ts: str) -> int:
    """How much the AI values keeping a card (lower = cheaper to throw away)."""
    return rv(card) + (10 if card[1] == ts else 0)


def other(who: str) -> str:
    return "a" if who == "p" else "p"


def sort_hand(hand: list, ts: str) -> list:
    # 6, 7, … A from left to right; trumps go last (also ascending)
    order = [s for s in "SHCD" if s != ts] + [ts]
    return sorted(hand, key=lambda c: (c[1] == ts, rv(c), order.index(c[1])))


# ---------------------------------------------------------------------------
# Game state
# ---------------------------------------------------------------------------

def new_state() -> dict:
    deck = [r + s for r in RANKS for s in SUITS]
    random.shuffle(deck)
    # deck[-1] is the top (drawn first), deck[0] is the face-up trump drawn last
    p = [deck.pop() for _ in range(6)]
    a = [deck.pop() for _ in range(6)]
    trump = deck[0]
    ts = trump[1]
    pt = [c for c in p if c[1] == ts]
    at = [c for c in a if c[1] == ts]
    if pt and at:
        first = "p" if min(map(rv, pt)) < min(map(rv, at)) else "a"
    elif at:
        first = "a"
    else:
        first = "p"
    st = {
        "deck": deck, "trump": trump, "ts": ts,
        "hands": {"p": p, "a": a},
        "table": [], "attacker": first, "taking": False,
        "discard": 0, "first_bout": True, "limit": 0, "result": None,
        "note": "",
    }
    st["limit"] = _bout_limit(st)
    return st


def _defender(st: dict) -> str:
    return other(st["attacker"])


def _bout_limit(st: dict) -> int:
    return min(5 if st["first_bout"] else 6, len(st["hands"][_defender(st)]))


def _undefended(st: dict) -> list:
    return [i for i, (a, d) in enumerate(st["table"]) if d is None]


def _table_ranks(st: dict) -> set:
    rs = set()
    for a, d in st["table"]:
        rs.add(a[0])
        if d:
            rs.add(d[0])
    return rs


def _can_add(st: dict, card: str) -> bool:
    if len(st["table"]) >= st["limit"]:
        return False
    if not st["table"]:
        return True
    return card[0] in _table_ranks(st)


def need(st: dict) -> str:
    """Who has to act now: 'p' or 'a'."""
    if not st["table"] or st["taking"] or not _undefended(st):
        return st["attacker"]
    return _defender(st)


def _refill(st: dict, who: str) -> None:
    h = st["hands"][who]
    while len(h) < 6 and st["deck"]:
        h.append(st["deck"].pop())


def _check_over(st: dict) -> bool:
    if st["deck"]:
        return False
    pe, ae = not st["hands"]["p"], not st["hands"]["a"]
    if pe and ae:
        st["result"] = "draw"
    elif pe:
        st["result"] = "win"
    elif ae:
        st["result"] = "lose"
    return st["result"] is not None


def _end_bout(st: dict, taken: bool) -> None:
    att, de = st["attacker"], _defender(st)
    cards = [c for pair in st["table"] for c in pair if c]
    if taken:
        st["hands"][de].extend(cards)
    else:
        st["discard"] += len(cards)
    st["table"] = []
    st["taking"] = False
    _refill(st, att)
    _refill(st, de)
    st["first_bout"] = False
    if not taken:
        st["attacker"] = de
    if not _check_over(st):
        st["limit"] = _bout_limit(st)


def _auto_resolve(st: dict) -> bool:
    """Ends the bout when nobody has a real choice left. Returns True if it ended."""
    if not st["table"] or st["result"]:
        return False
    att, de = st["attacker"], _defender(st)
    if st["taking"]:
        if len(st["table"]) >= st["limit"] or not any(_can_add(st, c) for c in st["hands"][att]):
            _end_bout(st, taken=True)
            st["note"] = ("Ты забираешь карты" if de == "p" else "Бот забирает карты")
            return True
        return False
    if not _undefended(st):
        if (not st["hands"][de] or len(st["table"]) >= st["limit"]
                or not any(_can_add(st, c) for c in st["hands"][att])):
            _end_bout(st, taken=False)
            st["note"] = "Бито"
            return True
    return False


# ---------------------------------------------------------------------------
# AI (medium strength)
# ---------------------------------------------------------------------------

def _ai_attack(st: dict) -> str:
    h, ts = st["hands"]["a"], st["ts"]
    deck_left = len(st["deck"])
    by_cost = sorted(h, key=lambda c: cost(c, ts))
    non_trump = [c for c in by_cost if c[1] != ts]
    pool = non_trump or by_cost
    # prefer leading with a rank we hold several of -- lets us toss more of it later
    low = pool[0]
    counts = {}
    for c in pool:
        counts[c[0]] = counts.get(c[0], 0) + 1
    pairs = [c for c in pool if counts[c[0]] >= 2 and rv(c) <= rv(low) + 2]
    if pairs and deck_left > 0:
        return pairs[0]
    if deck_left == 0 and len(st["hands"]["p"]) <= 2:
        # endgame: hit with the strongest card to keep the human from going out
        return by_cost[-1] if len(h) > 1 else by_cost[0]
    return low


def _ai_toss(st: dict) -> str | None:
    h, ts = st["hands"]["a"], st["ts"]
    deck_left = len(st["deck"])
    cands = sorted([c for c in h if _can_add(st, c)], key=lambda c: cost(c, ts))
    for c in cands:
        if c[1] == ts:
            if deck_left == 0 and (st["taking"] or len(h) <= 2):
                return c
            continue
        if deck_left == 0 or rv(c) <= RANKS.index("J") or (st["taking"] and rv(c) <= RANKS.index("Q")):
            return c
    return None


def _ai_defend_card(st: dict, att: str) -> str | None:
    """Card the AI beats `att` with, or None to take."""
    h, ts = st["hands"]["a"], st["ts"]
    opts = sorted([c for c in h if beats(c, att, ts)], key=lambda c: cost(c, ts))
    if not opts:
        return None
    best = opts[0]
    deck_left = len(st["deck"])
    if deck_left == 0:
        return best
    if best[1] == ts:
        # don't burn a high trump early, or any trump on cheap junk while the deck is fat
        if rv(best) >= RANKS.index("K") and deck_left > 4:
            return None
        if att[1] != ts and rv(att) <= RANKS.index("8") and deck_left >= 12 and len(st["table"]) == 1:
            return None
    return best


def _ai_step(st: dict) -> None:
    att, de = st["attacker"], _defender(st)
    if att == "a":
        if not st["table"]:
            c = _ai_attack(st)
            st["hands"]["a"].remove(c)
            st["table"].append([c, None])
            st["note"] = f"Бот ходит {nice(c)}"
            return
        c = _ai_toss(st)
        if c:
            st["hands"]["a"].remove(c)
            st["table"].append([c, None])
            st["note"] = f"Бот подкидывает {nice(c)}"
        elif st["taking"]:
            _end_bout(st, taken=True)
            st["note"] = "Ты забираешь карты"
        else:
            _end_bout(st, taken=False)
            st["note"] = "Бито — твой ход"
        return
    # AI defends
    i = _undefended(st)[0]
    a_card = st["table"][i][0]
    d = _ai_defend_card(st, a_card)
    if d is None:
        st["taking"] = True
        st["note"] = "Бот берёт — можешь подкинуть"
    else:
        st["hands"]["a"].remove(d)
        st["table"][i][1] = d
        st["note"] = f"Бот бьёт {nice(a_card)} картой {nice(d)}"


# ---------------------------------------------------------------------------
# Public view
# ---------------------------------------------------------------------------

def public(st: dict, game_id: int, bet: int, final: dict | None = None) -> dict:
    ts = st["ts"]
    hand = sort_hand(st["hands"]["p"], ts)
    view = {
        "game_id": game_id, "bet": bet,
        "hand": hand, "opp_count": len(st["hands"]["a"]),
        "deck_count": len(st["deck"]), "trump": st["trump"],
        "table": st["table"], "you_attack": st["attacker"] == "p",
        "taking": st["taking"], "discard": st["discard"],
        "limit": st["limit"], "note": st.get("note", ""),
        "result": st["result"],
    }
    if st["result"]:
        view["opp_hand"] = sort_hand(st["hands"]["a"], ts)
        view["turn"] = None
        view["playable"] = []
        view["can_take"] = view["can_done"] = False
        if final:
            view.update(final)
        return view
    turn = need(st)
    view["turn"] = "you" if turn == "p" else "ai"
    playable, can_take, can_done = [], False, False
    if turn == "p":
        if st["attacker"] == "p":
            playable = [c for c in hand if _can_add(st, c)]
            can_done = bool(st["table"])
        else:
            a_card = st["table"][_undefended(st)[0]][0]
            playable = [c for c in hand if beats(c, a_card, ts)]
            can_take = True
    view["playable"] = playable
    view["can_take"] = can_take
    view["can_done"] = can_done
    return view


def _run_ai(st: dict, game_id: int, bet: int, frames: list) -> None:
    for _ in range(80):
        if st["result"]:
            return
        if _auto_resolve(st):
            frames.append(public(st, game_id, bet))
            continue
        if need(st) != "a":
            return
        _ai_step(st)
        frames.append(public(st, game_id, bet))
    raise DurakError("ai loop")


# ---------------------------------------------------------------------------
# DB-backed actions
# ---------------------------------------------------------------------------

def _settle(conn, row, st: dict) -> dict:
    res = st["result"]
    status = {"win": "won", "lose": "lost", "draw": "draw"}[res]
    payout = int(round(row["bet"] * DURAK_WIN_MULT)) if res == "win" else (row["bet"] if res == "draw" else 0)
    cur = conn.execute(
        "UPDATE durak_games SET state = ?, status = ?, payout = ?, ver = ver + 1, resolved_at = ? "
        "WHERE id = ? AND status = 'active' AND ver = ?",
        (json.dumps(st), status, payout, _now(), row["id"], row["ver"]),
    )
    if cur.rowcount == 0:
        raise DurakError("игра уже изменилась, обнови")
    if payout:
        earned = payout if res == "win" else 0
        conn.execute(
            "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? WHERE telegram_id = ?",
            (payout, earned, row["user_id"]),
        )
    gems = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (row["user_id"],)).fetchone()["gems"]
    return {"payout": payout, "gems": gems}


def start(user_id: int, bet: int) -> dict:
    if not isinstance(bet, int) or bet < DURAK_MIN_BET or bet > DURAK_MAX_BET:
        raise DurakError(f"ставка от {DURAK_MIN_BET}")
    st = new_state()
    with db.get_conn() as conn:
        if conn.execute("SELECT 1 FROM durak_games WHERE user_id = ? AND status = 'active'", (user_id,)).fetchone():
            raise DurakError("уже есть начатая партия")
        row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if row is None or row["gems"] < bet:
            raise db.InsufficientGems()
        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (bet, user_id))
        cur = conn.execute(
            "INSERT INTO durak_games (user_id, bet, state, status, ver, created_at) VALUES (?, ?, ?, 'active', 0, ?)",
            (user_id, bet, "{}", _now()),
        )
        gid = cur.lastrowid
        st["note"] = "Ты ходишь первым" if st["attacker"] == "p" else "Бот ходит первым"
        frames = [public(st, gid, bet)]
        _run_ai(st, gid, bet, frames)
        conn.execute("UPDATE durak_games SET state = ? WHERE id = ?", (json.dumps(st), gid))
        gems = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()["gems"]
    out = dict(frames[-1])
    out["frames"] = frames
    out["gems"] = gems
    return out


def active(user_id: int) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM durak_games WHERE user_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    st = json.loads(row["state"])
    return public(st, row["id"], row["bet"])


def act(user_id: int, game_id: int, action: str, card: str | None) -> dict:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM durak_games WHERE id = ?", (game_id,)).fetchone()
        if row is None or row["user_id"] != user_id:
            raise DurakError("партия не найдена")
        if row["status"] != "active":
            raise DurakError("партия уже закончена")
        st = json.loads(row["state"])
        bet = row["bet"]
        frames = []
        if action == "surrender":
            st["result"] = "lose"
            st["note"] = "Ты сдался"
        else:
            if need(st) != "p":
                raise DurakError("сейчас ход бота")
            hand = st["hands"]["p"]
            if action == "play":
                if card not in hand:
                    raise DurakError("нет такой карты")
                if st["attacker"] == "p":
                    if not _can_add(st, card):
                        raise DurakError("эту карту сейчас нельзя")
                    hand.remove(card)
                    st["table"].append([card, None])
                    st["note"] = ""
                else:
                    i = _undefended(st)[0]
                    if not beats(card, st["table"][i][0], st["ts"]):
                        raise DurakError("этой картой не побить")
                    hand.remove(card)
                    st["table"][i][1] = card
                    st["note"] = ""
            elif action == "take":
                if st["attacker"] == "p" or not _undefended(st):
                    raise DurakError("сейчас нельзя взять")
                st["taking"] = True
                st["note"] = "Ты берёшь"
            elif action == "done":
                if st["attacker"] != "p" or not st["table"]:
                    raise DurakError("сейчас нельзя")
                if st["taking"]:
                    _end_bout(st, taken=True)
                    st["note"] = "Бот забрал карты — снова твой ход"
                elif not _undefended(st):
                    _end_bout(st, taken=False)
                    st["note"] = "Бито — ход бота"
                else:
                    raise DurakError("бот ещё не отбился")
            else:
                raise DurakError("неизвестное действие")
            frames.append(public(st, game_id, bet))
            _run_ai(st, game_id, bet, frames)
        if st["result"]:
            fin = _settle(conn, row, st)
            final = public(st, game_id, bet, fin)
            if frames:
                frames[-1] = final
            else:
                frames.append(final)
        else:
            cur = conn.execute(
                "UPDATE durak_games SET state = ?, ver = ver + 1 WHERE id = ? AND status = 'active' AND ver = ?",
                (json.dumps(st), game_id, row["ver"]),
            )
            if cur.rowcount == 0:
                raise DurakError("игра уже изменилась, обнови")
    out = dict(frames[-1])
    out["frames"] = frames
    return out


def history(limit: int = 20) -> list:
    excl = list(getattr(db, "LEADERBOARD_EXCLUDED_USERNAMES", []))
    ph = ",".join("?" for _ in excl) or "''"
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT g.bet, g.status, g.payout, g.resolved_at, u.username, u.first_name "
            "FROM durak_games g JOIN users u ON u.telegram_id = g.user_id "
            f"WHERE g.status != 'active' AND LOWER(COALESCE(u.username, '')) NOT IN ({ph}) "
            "ORDER BY g.id DESC LIMIT ?",
            excl + [limit],
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# FastAPI wiring
# ---------------------------------------------------------------------------

class _StartBody(BaseModel):
    initData: str
    bet: int


class _InitBody(BaseModel):
    initData: str


class _ActBody(BaseModel):
    initData: str
    game_id: int
    action: str
    card: str | None = None


def register(app, authenticate) -> None:
    init_table()

    @app.post("/api/durak/start")
    def durak_start(body: _StartBody):
        user = authenticate(body.initData)
        try:
            return start(user["telegram_id"], body.bet)
        except DurakError as e:
            raise HTTPException(400, str(e))
        except db.InsufficientGems:
            raise HTTPException(400, "not enough gems")

    @app.post("/api/durak/active")
    def durak_active(body: _InitBody):
        user = authenticate(body.initData)
        return {"game": active(user["telegram_id"])}

    @app.post("/api/durak/act")
    def durak_act(body: _ActBody):
        user = authenticate(body.initData)
        try:
            return act(user["telegram_id"], body.game_id, body.action, body.card)
        except DurakError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/durak/history")
    def durak_history(body: _InitBody):
        authenticate(body.initData)
        return {"rounds": history()}
