"""Косынка (Klondike) on gems -- "Vegas" rules, fully server-authoritative.

The server shuffles and keeps the deck; the client only ever learns a card's face when it is turned
face-up (tableau flip or drawn from the stock), so nobody can peek at hidden cards. Every move is
validated here. Rules: draw 3, at most KL_MAX_PASSES passes through the stock, no undo. The player
pays the bet up front and gets bet * cards_in_foundation / KL_CARDS_PER_BET back when they cash out
(or automatically when all 52 are home -- that pays 52/13 = 4x the bet).

Plugged into api.py with two lines at the end of it:
    import klondike_game
    app.include_router(klondike_game.make_router(_authenticate))
"""
import json
import random
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import database as db

KL_MIN_BET = 25
KL_MAX_BET = 100000
KL_DRAW = 3
KL_MAX_PASSES = 3          # passes through the stock (2 recycles)
KL_CARDS_PER_BET = 13      # every 13 cards home return one bet -> full win = 4x

_SCHEMA = """
CREATE TABLE IF NOT EXISTS klondike_rounds (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(telegram_id),
    bet          INTEGER NOT NULL,
    state        TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active',
    home         INTEGER NOT NULL DEFAULT 0,
    payout       INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_klondike_rounds_user ON klondike_rounds(user_id, status);
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


def init_tables():
    with db.get_conn() as conn:
        conn.executescript(_SCHEMA)


class KlondikeError(Exception):
    pass


# ---------------------------------------------------------------------------
# engine -- cards are physical ids 0..51; deck[id] = 0..51 value (suit*13 + rank)
# suits: 0 spades, 1 hearts, 2 diamonds, 3 clubs
# ---------------------------------------------------------------------------

def _suit(v): return v // 13
def _rank(v): return v % 13
def _red(v): return _suit(v) in (1, 2)


def new_state(bet):
    deck = list(range(52))
    random.SystemRandom().shuffle(deck)
    ids = list(range(52))
    tab = [[] for _ in range(7)]
    pos = 0
    for i in range(7):
        for j in range(i, 7):
            tab[j].append(ids[pos]); pos += 1
    up = [0] * 52
    for j in range(7):
        up[tab[j][-1]] = 1
    return {"deck": deck, "stock": ids[pos:], "waste": [], "found": [[], [], [], []], "tab": tab,
            "up": up, "passes": 1, "moves": 0, "bet": bet}


def _pile(s, ref):
    t = ref.get("t")
    if t == "tab":
        i = ref.get("i")
        if not isinstance(i, int) or not 0 <= i < 7: raise KlondikeError("bad pile")
        return s["tab"][i]
    if t == "found":
        i = ref.get("i")
        if not isinstance(i, int) or not 0 <= i < 4: raise KlondikeError("bad pile")
        return s["found"][i]
    if t == "waste":
        return s["waste"]
    raise KlondikeError("bad pile")


def _can_found(s, v, fi):
    f = s["found"][fi]
    if not f: return _rank(v) == 0
    top = s["deck"][f[-1]]
    return _suit(top) == _suit(v) and _rank(v) == _rank(top) + 1


def _can_tab(s, v, ti):
    col = s["tab"][ti]
    if not col: return _rank(v) == 12
    top = col[-1]
    if not s["up"][top]: return False
    tv = s["deck"][top]
    return _red(tv) != _red(v) and _rank(tv) == _rank(v) + 1


def apply_move(s, src, idx, dst):
    """Returns list of newly revealed card ids."""
    if src.get("t") == dst.get("t") and src.get("i") == dst.get("i"):
        raise KlondikeError("same pile")
    sp = _pile(s, src)
    dp = _pile(s, dst)
    if dst["t"] == "waste": raise KlondikeError("bad target")
    if not isinstance(idx, int) or not 0 <= idx < len(sp): raise KlondikeError("bad card")
    if src["t"] != "tab" and idx != len(sp) - 1: raise KlondikeError("bad card")
    if not s["up"][sp[idx]]: raise KlondikeError("card is face down")
    v = s["deck"][sp[idx]]
    if dst["t"] == "found":
        if idx != len(sp) - 1 or not _can_found(s, v, dst["i"]): raise KlondikeError("illegal move")
    else:
        if not _can_tab(s, v, dst["i"]): raise KlondikeError("illegal move")
    cards = sp[idx:]
    del sp[idx:]
    dp.extend(cards)
    revealed = []
    if src["t"] == "tab" and sp and not s["up"][sp[-1]]:
        s["up"][sp[-1]] = 1
        revealed.append(sp[-1])
    s["moves"] += 1
    return revealed


def apply_draw(s):
    revealed = []
    if s["stock"]:
        for _ in range(min(KL_DRAW, len(s["stock"]))):
            c = s["stock"].pop()
            s["up"][c] = 1
            s["waste"].append(c)
            revealed.append(c)
    elif s["waste"]:
        if s["passes"] >= KL_MAX_PASSES: raise KlondikeError("no passes left")
        while s["waste"]:
            c = s["waste"].pop()
            s["up"][c] = 0
            s["stock"].append(c)
        s["passes"] += 1
    else:
        raise KlondikeError("stock is empty")
    s["moves"] += 1
    return revealed


def home_count(s):
    return sum(len(f) for f in s["found"])


def payout_for(bet, home):
    return bet * home // KL_CARDS_PER_BET


def view(round_id, s):
    """What the client may know: layout of ids, which are face-up, and faces of face-up cards only."""
    faces = {str(c): s["deck"][c] for c in range(52) if s["up"][c]}
    return {"round_id": round_id, "bet": s["bet"], "stock": s["stock"], "waste": s["waste"], "found": s["found"],
            "tab": s["tab"], "up": s["up"], "faces": faces, "passes": s["passes"], "max_passes": KL_MAX_PASSES,
            "draw": KL_DRAW, "moves": s["moves"], "home": home_count(s), "payout_now": payout_for(s["bet"], home_count(s))}


# ---------------------------------------------------------------------------
# db operations
# ---------------------------------------------------------------------------

def _active(conn, user_id):
    return conn.execute("SELECT id, state FROM klondike_rounds WHERE user_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
                        (user_id,)).fetchone()


def get_active(user_id):
    with db.get_conn() as conn:
        row = _active(conn, user_id)
        gems = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    return {"round": view(row["id"], json.loads(row["state"])) if row else None, "gems": gems["gems"] if gems else 0}


def _settle(conn, user_id, round_id, s):
    home = home_count(s)
    pay = payout_for(s["bet"], home)
    cur = conn.execute("UPDATE klondike_rounds SET status = 'done', state = ?, home = ?, payout = ?, finished_at = ? "
                       "WHERE id = ? AND user_id = ? AND status = 'active'",
                       (json.dumps(s), home, pay, _now(), round_id, user_id))
    if cur.rowcount != 1:
        raise KlondikeError("round is already finished")
    if pay > 0:
        conn.execute("UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? WHERE telegram_id = ?",
                     (pay, max(0, pay - s["bet"]), user_id))
    gems = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()["gems"]
    return {"finished": True, "home": home, "payout": pay, "bet": s["bet"], "gems": gems,
            "deck": s["deck"]}


def start(user_id, bet):
    if not isinstance(bet, int) or isinstance(bet, bool) or bet < KL_MIN_BET:
        raise KlondikeError(f"bet must be a whole number >= {KL_MIN_BET}")
    if bet > KL_MAX_BET:
        raise KlondikeError(f"bet must be <= {KL_MAX_BET}")
    with db.get_conn() as conn:
        if _active(conn, user_id):
            raise KlondikeError("finish the current game first")
        row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if row is None or row["gems"] < bet:
            raise db.InsufficientGems()
        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (bet, user_id))
        s = new_state(bet)
        cur = conn.execute("INSERT INTO klondike_rounds (user_id, bet, state, created_at) VALUES (?, ?, ?, ?)",
                           (user_id, bet, json.dumps(s), _now()))
        gems = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()["gems"]
    return {"round": view(cur.lastrowid, s), "gems": gems}


def move(user_id, round_id, kind, src=None, idx=None, dst=None):
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _active(conn, user_id)
        if not row or row["id"] != round_id:
            raise KlondikeError("no such active game")
        s = json.loads(row["state"])
        if kind == "draw":
            revealed = apply_draw(s)
        elif kind == "move":
            if not isinstance(src, dict) or not isinstance(dst, dict): raise KlondikeError("bad move")
            revealed = apply_move(s, src, idx, dst)
        else:
            raise KlondikeError("bad move")
        res = {"reveal": {str(c): s["deck"][c] for c in revealed}, "home": home_count(s),
               "payout_now": payout_for(s["bet"], home_count(s)), "passes": s["passes"], "moves": s["moves"]}
        if home_count(s) == 52:
            res.update(_settle(conn, user_id, round_id, s))
        else:
            conn.execute("UPDATE klondike_rounds SET state = ?, home = ? WHERE id = ?", (json.dumps(s), home_count(s), round_id))
    return res


def cashout(user_id, round_id):
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _active(conn, user_id)
        if not row or row["id"] != round_id:
            raise KlondikeError("no such active game")
        return _settle(conn, user_id, round_id, json.loads(row["state"]))


def house_stats():
    with db.get_conn() as conn:
        r = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(bet),0) wagered, COALESCE(SUM(payout),0) paid, "
                         "COALESCE(AVG(home),0) avg_home, SUM(home = 52) wins FROM klondike_rounds WHERE status = 'done'").fetchone()
    return dict(r)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class _Init(BaseModel):
    initData: str


class _Start(_Init):
    bet: int


class _Move(_Init):
    round_id: int
    kind: str
    src: dict | None = None
    idx: int | None = None
    dst: dict | None = None


class _Cashout(_Init):
    round_id: int


def make_router(authenticate):
    init_tables()
    r = APIRouter()

    def _wrap(fn):
        try:
            return fn()
        except KlondikeError as e:
            raise HTTPException(400, str(e))
        except db.InsufficientGems:
            raise HTTPException(400, "not enough gems")

    @r.post("/api/klondike/state")
    def kl_state(body: _Init):
        user = authenticate(body.initData)
        out = get_active(user["telegram_id"])
        out.update(min_bet=KL_MIN_BET, max_bet=KL_MAX_BET, cards_per_bet=KL_CARDS_PER_BET, max_passes=KL_MAX_PASSES, draw=KL_DRAW)
        return out

    @r.post("/api/klondike/start")
    def kl_start(body: _Start):
        user = authenticate(body.initData)
        return _wrap(lambda: start(user["telegram_id"], body.bet))

    @r.post("/api/klondike/move")
    def kl_move(body: _Move):
        user = authenticate(body.initData)
        return _wrap(lambda: move(user["telegram_id"], body.round_id, body.kind, body.src, body.idx, body.dst))

    @r.post("/api/klondike/cashout")
    def kl_cashout(body: _Cashout):
        user = authenticate(body.initData)
        return _wrap(lambda: cashout(user["telegram_id"], body.round_id))

    return r
