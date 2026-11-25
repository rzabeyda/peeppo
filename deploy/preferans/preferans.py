"""
Peeppo -- «Преферанс» (Сочи, упрощённый, короткая пуля) на гемы.
Игрок + 2 бота. Торговля (6♠ … 10 БК, мизер), прикуп и снос, вист/пас, распасы.
Партия = PREF_DEALS раздач; в конце подсчёт в вистах:
    total_i = 10*(пуля_i - гора_i) + (висты i на других) - (висты других на i)
    итог_i  = total_i - среднее(total)
Итог > 0 -> выплата bet * PREF_WIN_MULT, = 0 -> ставка возвращается, < 0 -> 0.

Подключение в api.py (одна строка в конце файла):
    import preferans as _pref; _pref.register(app, _authenticate)
"""

import json
import math
import random
from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import BaseModel

import database as db

PREF_MIN_BET = 1
PREF_MAX_BET = 1_000_000
PREF_WIN_MULT = 1.9
PREF_DEALS = 6

RANKS = "789TJQKA"
SUITS = "SCDH"          # ♠ < ♣ < ♦ < ♥ (порядок старшинства в торговле)
SUIT_SYM = {"S": "♠", "C": "♣", "D": "♦", "H": "♥", "N": "БК"}
NAMES = ["Ты", "Бот Миша", "Бот Гоша"]

CONTRACTS = [f"{l}{s}" for l in (6, 7, 8) for s in "SCDHN"] + ["MIS"] + [f"{l}{s}" for l in (9, 10) for s in "SCDHN"]
CIDX = {c: i for i, c in enumerate(CONTRACTS)}
COST = {6: 2, 7: 4, 8: 6, 9: 8, 10: 10}
VIST_NORM = {6: 4, 7: 2, 8: 1, 9: 1, 10: 0}

SCHEMA = """
CREATE TABLE IF NOT EXISTS pref_games (
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
CREATE INDEX IF NOT EXISTS idx_pref_user_status ON pref_games(user_id, status);
"""


class PrefError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_table() -> None:
    with db.get_conn() as conn:
        conn.executescript(SCHEMA)


def rv(c: str) -> int:
    return RANKS.index(c[0])


def nice(c: str) -> str:
    return ("10" if c[0] == "T" else c[0]) + SUIT_SYM[c[1]]


def clevel(contract: str) -> int:
    return 10 if contract.startswith("10") else int(contract[0])


def csuit(contract: str) -> str:
    return contract[-1]


def contract_name(c: str) -> str:
    if c == "MIS":
        return "мизер"
    s = csuit(c)
    return f"{clevel(c)}{'БК' if s == 'N' else SUIT_SYM[s]}"


def nxt(i: int) -> int:
    return (i + 1) % 3


def sort_hand(h: list) -> list:
    order = "SDCH"
    return sorted(h, key=lambda c: (order.index(c[1]), -rv(c)))


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def new_game() -> dict:
    st = {
        "deal_no": 0, "max_deals": PREF_DEALS, "dealer": random.randrange(3),
        "pool": [0, 0, 0], "gora": [0, 0, 0], "vists": [[0, 0, 0] for _ in range(3)],
        "result": None, "log": [],
    }
    _new_deal(st)
    return st


def _new_deal(st: dict) -> None:
    deck = [r + s for r in RANKS for s in SUITS]
    random.shuffle(deck)
    st["deal_no"] += 1
    st["hands"] = [deck[0:10], deck[10:20], deck[20:30]]
    st["prikup"] = deck[30:32]
    st["prikup_shown"] = False
    st["discard"] = []
    st["phase"] = "bidding"
    first = nxt(st["dealer"])
    st["first"] = first
    st["turn"] = first
    st["bids"] = [None, None, None]
    st["passed"] = [False, False, False]
    st["high"] = None
    st["declarer"] = None
    st["contract"] = None
    st["vist"] = [None, None, None]
    st["trick"] = []
    st["last_trick"] = []
    st["leader"] = first
    st["tricks"] = [0, 0, 0]
    st["played"] = []
    st["summary"] = None
    st["note"] = f"Раздача {st['deal_no']} из {st['max_deals']}"


def _trump(st: dict) -> str | None:
    c = st["contract"]
    if not c or c == "MIS" or c == "RASP":
        return None
    s = csuit(c)
    return None if s == "N" else s


# ---------------------------------------------------------------------------
# Hand evaluation (bots)
# ---------------------------------------------------------------------------

def _suits(h: list) -> dict:
    d = {s: [] for s in SUITS}
    for c in h:
        d[c[1]].append(c)
    for s in d:
        d[s].sort(key=rv, reverse=True)
    return d


def est_tricks(h: list, trump: str | None) -> float:
    ss = _suits(h)
    t = 0.0
    for s, cs in ss.items():
        n = len(cs)
        rs = {c[0] for c in cs}
        if s == trump:
            hon = ("A" in rs) + ("K" in rs and n >= 2) + ("Q" in rs and n >= 3)
            t += min(n, hon + max(0, n - 2.5))
        else:
            if "A" in rs:
                t += 1
                if "K" in rs:
                    t += 1
                    if "Q" in rs and n >= 3:
                        t += 1
                        if trump is None:
                            t += max(0, n - 3) * 0.9
            elif "K" in rs and n >= 2:
                t += 0.7
            if trump is None and "A" not in rs and n <= 2 and n > 0:
                t -= 0.6  # weak short suit without trumps -- opponents run it
    return t


def misere_ok(h: list, strict: bool = True) -> bool:
    bad = 0
    for s, cs in _suits(h).items():
        cs = sorted(cs, key=rv)
        for i, c in enumerate(cs):
            if rv(c) > 2 * i:
                bad += 1
    return bad == 0 if strict else bad <= 1


def _capacity(h: list, bonus: float) -> dict:
    cap = {}
    for s in list(SUITS) + ["N"]:
        e = est_tricks(h, None if s == "N" else s) + bonus
        cap[s] = int(math.floor(e))
    return cap


def _bot_bid(st: dict, p: int) -> str:
    h = st["hands"][p]
    hi = CIDX[st["high"]] if st["high"] else -1
    if st["bids"][p] is None and hi < CIDX["MIS"] and misere_ok(h):
        cap = _capacity(h, 1.0)
        if max(cap.values()) < 7:
            return "MIS"
    if st["bids"][p] == "MIS":
        return "pass"
    cap = _capacity(h, 1.0)
    for i in range(hi + 1, len(CONTRACTS)):
        c = CONTRACTS[i]
        if c == "MIS":
            continue
        if clevel(c) <= cap[csuit(c)]:
            return c
    return "pass"


def _best_discard(h12: list, trump: str | None) -> list:
    ss = _suits(h12)

    def keep_score(c):
        s = c[1]
        n = len(ss[s])
        sc = rv(c) * 2
        if s == trump:
            sc += 40
        if c[0] == "A":
            sc += 30
        if c[0] == "K" and any(x[0] == "A" for x in ss[s]):
            sc += 25
        sc += n * 1.5  # long suits are worth keeping
        return sc
    return sorted(h12, key=keep_score)[:2]


def _bot_declare(st: dict, p: int) -> tuple:
    h12 = st["hands"][p]
    if st["high"] == "MIS":
        # dump the two most dangerous cards
        disc = sorted(h12, key=lambda c: rv(c), reverse=True)
        best = None
        for a in range(len(h12)):
            for b in range(a + 1, len(h12)):
                rest = [c for k, c in enumerate(h12) if k not in (a, b)]
                if misere_ok(rest):
                    return [h12[a], h12[b]], "MIS"
                score = sum(1 for s, cs in _suits(rest).items() for i, c in enumerate(sorted(cs, key=rv)) if rv(c) > 2 * i)
                if best is None or score < best[0]:
                    best = (score, [h12[a], h12[b]])
        return best[1], "MIS"
    hi = CIDX[st["high"]]
    best = None
    for s in list(SUITS) + ["N"]:
        tr = None if s == "N" else s
        disc = _best_discard(h12, tr)
        rest = [c for c in h12 if c not in disc]
        e = est_tricks(rest, tr)
        if best is None or e > best[0] + 0.01:
            best = (e, s, disc)
    e, s, disc = best
    # lowest contract in suit s that is >= the bid
    need = None
    for l in range(6, 11):
        c = f"{l}{s}"
        if CIDX[c] >= hi:
            need = l
            break
    lvl = max(need or 10, min(10, int(math.floor(e))))
    c = f"{lvl}{s}"
    if CIDX[c] < hi:
        c = st["high"]
        disc = _best_discard(h12, None if csuit(c) == "N" else csuit(c))
    return disc, c


def _bot_vist(st: dict, p: int) -> str:
    c = st["contract"]
    lvl = clevel(c)
    tr = _trump(st)
    h = st["hands"][p]
    ss = _suits(h)
    e = 0.0
    for s, cs in ss.items():
        rs = {x[0] for x in cs}
        if s == tr:
            e += ("A" in rs) + ("K" in rs and len(cs) >= 2) * 0.8 + ("Q" in rs and len(cs) >= 3) * 0.5
            e += max(0, len(cs) - 3) * 0.7
        else:
            e += ("A" in rs) + ("K" in rs and len(cs) >= 2 and len(cs) <= 4) * 0.5
    other = [x for x in range(3) if x not in (p, st["declarer"])][0]
    other_visted = st["vist"][other] == "vist"
    need = {6: 1.2, 7: 1.0, 8: 1.0, 9: 1.2, 10: 1.5}[lvl]
    if lvl == 6 and not other_visted:
        need = 0.8
    return "vist" if e >= need else "pass"


# ---------------------------------------------------------------------------
# Card play
# ---------------------------------------------------------------------------

def _legal(st: dict, p: int) -> list:
    h = st["hands"][p]
    if not st["trick"]:
        return list(h)
    lead = st["trick"][0][1][1]
    f = [c for c in h if c[1] == lead]
    if f:
        return f
    tr = _trump(st)
    if tr:
        t = [c for c in h if c[1] == tr]
        if t:
            return t
    return list(h)


def _winner_of(trick: list, tr: str | None) -> tuple:
    lead = trick[0][1][1]
    best = trick[0]
    for p, c in trick[1:]:
        b = best[1]
        if tr and c[1] == tr and b[1] != tr:
            best = (p, c)
        elif c[1] == b[1] and rv(c) > rv(b):
            best = (p, c)
    return best


def _beats(c: str, cur: str, lead: str, tr: str | None) -> bool:
    if c[1] == cur[1]:
        return rv(c) > rv(cur)
    return bool(tr) and c[1] == tr and cur[1] != tr


def _is_master(st: dict, p: int, c: str) -> bool:
    gone = set(st["played"]) | set(st["hands"][p])
    if p == st["declarer"]:
        gone |= set(st["discard"])
    for r in RANKS[rv(c) + 1:]:
        if r + c[1] not in gone:
            return False
    return True


def _team(st: dict, a: int, b: int) -> bool:
    if st["contract"] in ("RASP",):
        return a == b
    d = st["declarer"]
    return (a == d) == (b == d)


def _bot_play(st: dict, p: int) -> str:
    legal = _legal(st, p)
    tr = _trump(st)
    mode = st["contract"]
    trick = st["trick"]
    last = len(trick) == 2
    by_low = sorted(legal, key=rv)

    if mode in ("RASP", "MIS") and (mode == "RASP" or p == st["declarer"]):
        # avoid tricks
        if not trick:
            ss = _suits(st["hands"][p])
            # lead the lowest card of the suit where we're safest
            return min(legal, key=lambda c: (rv(c), -len(ss[c[1]])))
        w = _winner_of(trick, tr)
        lead = trick[0][1][1]
        under = [c for c in legal if not _beats(c, w[1], lead, tr)]
        if under:
            if legal[0][1] != lead:  # void -- dump the highest card
                return max(legal, key=rv)
            return max(under, key=rv)
        return max(legal, key=rv) if last else min(legal, key=rv)

    if mode == "MIS":
        # defenders: make the declarer take a trick
        d = st["declarer"]
        if not trick:
            dh = len(st["hands"][d])
            return by_low[0]
        w = _winner_of(trick, tr)
        lead = trick[0][1][1]
        declarer_played = any(pp == d for pp, _ in trick)
        if declarer_played and w[0] == d:
            under = [c for c in legal if not _beats(c, w[1], lead, tr)]
            if under:
                return max(under, key=rv)
            return by_low[0]
        if legal[0][1] != lead:
            return max(legal, key=rv)
        return by_low[0]

    # normal contract
    if not trick:
        masters = [c for c in legal if _is_master(st, p, c)]
        d = st["declarer"]
        if p == d and tr:
            opp_trumps = [r + tr for r in RANKS if r + tr not in st["played"] and r + tr not in st["hands"][p] and r + tr not in st["discard"]]
            my_tr = [c for c in legal if c[1] == tr]
            mt = [c for c in masters if c[1] == tr]
            if opp_trumps and mt:
                return max(mt, key=rv)
        side_m = [c for c in masters if c[1] != tr]
        if side_m:
            return max(side_m, key=rv)
        ss = _suits(st["hands"][p])
        side = [c for c in legal if c[1] != tr]
        pool = side or legal
        return min(pool, key=lambda c: (-len(ss[c[1]]), rv(c)))
    w = _winner_of(trick, tr)
    lead = trick[0][1][1]
    partner_winning = _team(st, w[0], p) and w[0] != p
    winners = [c for c in legal if _beats(c, w[1], lead, tr)]
    if partner_winning and (last or _is_master(st, w[0], w[1]) or (w[1][1] == tr and lead != tr)):
        return _cheapest(legal, tr)
    if winners:
        winners.sort(key=lambda c: (c[1] == tr, rv(c)))
        if last:
            return winners[0]
        m = [c for c in winners if _is_master(st, p, c)]
        if m:
            return min(m, key=lambda c: (c[1] == tr, rv(c)))
        if winners[0][1] == tr and lead != tr:
            return winners[0]  # ruff -- third hand can only overruff
        return winners[0] if rv(winners[0]) >= RANKS.index("Q") else _cheapest(legal, tr)
    return _cheapest(legal, tr)


def _cheapest(legal: list, tr: str | None) -> str:
    return min(legal, key=lambda c: (c[1] == tr, rv(c)))


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------

def _bid_next(st: dict) -> None:
    active = [i for i in range(3) if not st["passed"][i]]
    if not active:
        st["contract"] = "RASP"
        st["phase"] = "play"
        st["leader"] = st["first"]
        st["turn"] = st["first"]
        st["note"] = "Все спасовали — распасы"
        return
    if len(active) == 1 and st["bids"][active[0]] not in (None, "pass"):
        d = active[0]
        st["declarer"] = d
        st["phase"] = "discard"
        st["turn"] = d
        st["hands"][d] = st["hands"][d] + st["prikup"]
        st["prikup_shown"] = True
        st["note"] = f"{NAMES[d]} играет ({contract_name(st['high'])}), берёт прикуп"
        return
    t = nxt(st["turn"])
    while st["passed"][t]:
        t = nxt(t)
    st["turn"] = t


def _apply_bid(st: dict, p: int, bid: str) -> None:
    st.setdefault("said", [None, None, None])
    if bid == "pass":
        st["passed"][p] = True
        st["said"][p] = "pass"
        st["note"] = f"{NAMES[p]}: пас"
    else:
        st["bids"][p] = bid
        st["high"] = bid
        st["said"][p] = bid
        st["note"] = f"{NAMES[p]}: {contract_name(bid)}"
    _bid_next(st)


def _allowed_bids(st: dict, p: int) -> list:
    hi = CIDX[st["high"]] if st["high"] else -1
    out = []
    if st["bids"][p] == "MIS":
        return out
    for i in range(hi + 1, len(CONTRACTS)):
        c = CONTRACTS[i]
        if c == "MIS" and st["bids"][p] is not None:
            continue
        out.append(c)
    return out


def _apply_declare(st: dict, p: int, disc: list, contract: str) -> None:
    h = st["hands"][p]
    for c in disc:
        h.remove(c)
    st["discard"] = list(disc)
    st["contract"] = contract
    st["note"] = f"{NAMES[p]} заказывает {contract_name(contract)}"
    if contract == "MIS":
        st["phase"] = "play"
        st["leader"] = st["first"]
        st["turn"] = st["first"]
        return
    st["phase"] = "vist"
    st["turn"] = nxt(p)


def _apply_vist(st: dict, p: int, v: str) -> None:
    st["vist"][p] = v
    st["note"] = f"{NAMES[p]}: {'вист' if v == 'vist' else 'пас'}"
    d = st["declarer"]
    o = nxt(p)
    if o != d and st["vist"][o] is None:
        st["turn"] = o
        return
    if all(st["vist"][i] != "vist" for i in range(3) if i != d):
        # both defenders passed -- the contract is counted as made without play
        lvl = clevel(st["contract"])
        st["pool"][d] += COST[lvl]
        st["summary"] = {
            "text": f"Оба спасовали — {NAMES[d]} пишет {COST[lvl]} в пулю за {contract_name(st['contract'])}",
            "tricks": None,
        }
        st["phase"] = "summary"
        st["turn"] = 0
        return
    st["phase"] = "play"
    st["leader"] = st["first"]
    st["turn"] = st["first"]


def _apply_play(st: dict, p: int, c: str) -> bool:
    """Returns True when this card completed a trick (caller snapshots before resolve)."""
    st["hands"][p].remove(c)
    st["trick"].append([p, c])
    st["played"].append(c)
    st["note"] = ""
    if len(st["trick"]) < 3:
        st["turn"] = nxt(p)
        return False
    return True


def _resolve_trick(st: dict) -> None:
    tr = _trump(st)
    w = _winner_of(st["trick"], tr)[0]
    st["tricks"][w] += 1
    st["last_trick"] = st["trick"]
    st["trick"] = []
    st["leader"] = w
    st["turn"] = w
    st["note"] = "Взятка твоя" if w == 0 else f"Взятку берёт {NAMES[w]}"
    if not any(st["hands"]):
        _score_deal(st)


def _score_deal(st: dict) -> None:
    t = st["tricks"]
    c = st["contract"]
    lines = []
    if c == "RASP":
        for i in range(3):
            if t[i] == 0:
                st["pool"][i] += 1
                lines.append(f"{NAMES[i]}: 0 взяток, +1 в пулю")
            else:
                st["gora"][i] += t[i]
                lines.append(f"{NAMES[i]}: {t[i]} взят., +{t[i]} в гору")
    elif c == "MIS":
        d = st["declarer"]
        if t[d] == 0:
            st["pool"][d] += 10
            lines.append(f"{NAMES[d]} сыграл мизер: +10 в пулю")
        else:
            st["gora"][d] += 10 * t[d]
            lines.append(f"{NAMES[d]} поймался на мизере ({t[d]} взят.): +{10 * t[d]} в гору")
    else:
        d = st["declarer"]
        lvl = clevel(c)
        cost = COST[lvl]
        defs = [i for i in range(3) if i != d]
        visters = [i for i in defs if st["vist"][i] == "vist"]
        dt = sum(t[i] for i in defs)
        if t[d] >= lvl:
            st["pool"][d] += cost
            lines.append(f"{NAMES[d]} сыграл {contract_name(c)} ({t[d]} взят.): +{cost} в пулю")
        else:
            u = lvl - t[d]
            st["gora"][d] += cost * u
            lines.append(f"{NAMES[d]} без {u}: +{cost * u} в гору")
            for v in visters:
                st["vists"][v][d] += cost * u
        # defenders' vists for their tricks
        if len(visters) == 1:
            v = visters[0]
            st["vists"][v][d] += cost * dt
            lines.append(f"{NAMES[v]} вистовал: {dt} взят., +{cost * dt} вистов")
            short = VIST_NORM[lvl] - dt
            if short > 0 and t[d] >= lvl:
                st["gora"][v] += cost * short
                lines.append(f"{NAMES[v]} недобрал {short}: +{cost * short} в гору")
        elif len(visters) == 2:
            share = VIST_NORM[lvl] / 2
            for v in visters:
                st["vists"][v][d] += cost * t[v]
                lines.append(f"{NAMES[v]}: {t[v]} взят., +{cost * t[v]} вистов")
                short = share - t[v]
                if short > 0 and t[d] >= lvl and dt < VIST_NORM[lvl]:
                    g = int(math.ceil(cost * short))
                    st["gora"][v] += g
                    lines.append(f"{NAMES[v]} недобрал: +{g} в гору")
    st["summary"] = {"text": "\n".join(lines), "tricks": list(t)}
    st["phase"] = "summary"
    st["turn"] = 0


def totals(st: dict) -> list:
    raw = []
    for i in range(3):
        w = sum(st["vists"][i]) - sum(st["vists"][j][i] for j in range(3))
        raw.append(10 * (st["pool"][i] - st["gora"][i]) + w)
    m = sum(raw) / 3
    return [round(x - m, 1) for x in raw]


def _next_deal(st: dict) -> None:
    if st["deal_no"] >= st["max_deals"]:
        tot = totals(st)
        st["phase"] = "over"
        st["result"] = "win" if tot[0] > 0.05 else ("lose" if tot[0] < -0.05 else "draw")
        st["note"] = "Пуля закрыта"
        return
    st["dealer"] = nxt(st["dealer"])
    _new_deal(st)


def need(st: dict) -> int | None:
    if st["phase"] in ("over",):
        return None
    if st["phase"] == "summary":
        return 0
    return st["turn"]


def _bot_step(st: dict, frames: list, gid: int, bet: int) -> None:
    p = st["turn"]
    ph = st["phase"]
    if ph == "bidding":
        _apply_bid(st, p, _bot_bid(st, p))
    elif ph == "discard":
        frames.append(public(st, gid, bet))  # show the prikup first
        disc, c = _bot_declare(st, p)
        _apply_declare(st, p, disc, c)
    elif ph == "vist":
        _apply_vist(st, p, _bot_vist(st, p))
    elif ph == "play":
        c = _bot_play(st, p)
        if _apply_play(st, p, c):
            frames.append(public(st, gid, bet))
            _resolve_trick(st)
    frames.append(public(st, gid, bet))


def _run_bots(st: dict, gid: int, bet: int, frames: list) -> None:
    for _ in range(200):
        n = need(st)
        if n is None or n == 0:
            return
        _bot_step(st, frames, gid, bet)
    raise PrefError("bot loop")


# ---------------------------------------------------------------------------
# Public view
# ---------------------------------------------------------------------------

def public(st: dict, gid: int, bet: int, final: dict | None = None) -> dict:
    ph = st["phase"]
    hand = sort_hand(st["hands"][0])
    v = {
        "game_id": gid, "bet": bet, "deal_no": st["deal_no"], "max_deals": st["max_deals"],
        "dealer": st["dealer"], "first": st["first"], "phase": ph, "turn": st["turn"],
        "names": NAMES, "hand": hand, "counts": [len(h) for h in st["hands"]],
        "said": st.get("said", [None, None, None]), "high": st["high"],
        "declarer": st["declarer"], "contract": st["contract"],
        "vist": st["vist"], "trick": st["trick"], "last_trick": st["last_trick"],
        "tricks": st["tricks"], "note": st.get("note", ""),
        "prikup": st["prikup"] if st["prikup_shown"] else None,
        "pool": st["pool"], "gora": st["gora"],
        "vists_net": [sum(st["vists"][i]) - sum(st["vists"][j][i] for j in range(3)) for i in range(3)],
        "totals": totals(st), "summary": st["summary"], "result": st["result"],
        "legal": [], "allowed_bids": [], "allowed_contracts": [],
    }
    if ph == "summary" and st["summary"]:
        pass
    if ph == "over" or st["result"]:
        if final:
            v.update(final)
        return v
    if need(st) == 0:
        if ph == "bidding":
            v["allowed_bids"] = _allowed_bids(st, 0)
        elif ph == "discard":
            if st["high"] == "MIS":
                v["allowed_contracts"] = ["MIS"]
            else:
                hi = CIDX[st["high"]]
                v["allowed_contracts"] = [c for c in CONTRACTS if c != "MIS" and CIDX[c] >= hi]
        elif ph == "play":
            v["legal"] = _legal(st, 0)
    return v


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def _settle(conn, row, st: dict) -> dict:
    res = st["result"]
    status = {"win": "won", "lose": "lost", "draw": "draw"}[res]
    payout = int(round(row["bet"] * PREF_WIN_MULT)) if res == "win" else (row["bet"] if res == "draw" else 0)
    cur = conn.execute(
        "UPDATE pref_games SET state = ?, status = ?, payout = ?, ver = ver + 1, resolved_at = ? "
        "WHERE id = ? AND status = 'active' AND ver = ?",
        (json.dumps(st), status, payout, _now(), row["id"], row["ver"]),
    )
    if cur.rowcount == 0:
        raise PrefError("игра уже изменилась, обнови")
    if payout:
        conn.execute(
            "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? WHERE telegram_id = ?",
            (payout, payout if res == "win" else 0, row["user_id"]),
        )
    gems = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (row["user_id"],)).fetchone()["gems"]
    return {"payout": payout, "gems": gems}


def start(user_id: int, bet: int) -> dict:
    if not isinstance(bet, int) or bet < PREF_MIN_BET or bet > PREF_MAX_BET:
        raise PrefError(f"ставка от {PREF_MIN_BET}")
    st = new_game()
    with db.get_conn() as conn:
        if conn.execute("SELECT 1 FROM pref_games WHERE user_id = ? AND status = 'active'", (user_id,)).fetchone():
            raise PrefError("уже есть начатая пуля")
        row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if row is None or row["gems"] < bet:
            raise db.InsufficientGems()
        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (bet, user_id))
        cur = conn.execute(
            "INSERT INTO pref_games (user_id, bet, state, status, ver, created_at) VALUES (?, ?, '{}', 'active', 0, ?)",
            (user_id, bet, _now()),
        )
        gid = cur.lastrowid
        frames = [public(st, gid, bet)]
        _run_bots(st, gid, bet, frames)
        conn.execute("UPDATE pref_games SET state = ? WHERE id = ?", (json.dumps(st), gid))
        gems = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()["gems"]
    out = dict(frames[-1])
    out["frames"] = frames
    out["gems"] = gems
    return out


def active(user_id: int) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pref_games WHERE user_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    return public(json.loads(row["state"]), row["id"], row["bet"])


def apply_human(st: dict, action: str, bid: str | None, card: str | None, cards: list | None, contract: str | None) -> None:
    if need(st) != 0:
        raise PrefError("сейчас ход бота")
    ph = st["phase"]
    if action == "bid" and ph == "bidding":
        if bid != "pass" and bid not in _allowed_bids(st, 0):
            raise PrefError("такую заявку нельзя")
        _apply_bid(st, 0, bid)
    elif action == "declare" and ph == "discard":
        h = st["hands"][0]
        if not cards or len(cards) != 2 or len(set(cards)) != 2 or any(c not in h for c in cards):
            raise PrefError("выбери 2 карты в снос")
        allowed = public(st, 0, 0)["allowed_contracts"]
        if contract not in allowed:
            raise PrefError("такой контракт нельзя")
        _apply_declare(st, 0, cards, contract)
    elif action in ("vist", "pass") and ph == "vist":
        _apply_vist(st, 0, action)
    elif action == "play" and ph == "play":
        if card not in _legal(st, 0):
            raise PrefError("этой картой ходить нельзя")
        if _apply_play(st, 0, card):
            return "trick"
    elif action == "next" and ph == "summary":
        _next_deal(st)
    else:
        raise PrefError("сейчас так нельзя")
    return None


def act(user_id: int, gid: int, action: str, bid=None, card=None, cards=None, contract=None) -> dict:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM pref_games WHERE id = ?", (gid,)).fetchone()
        if row is None or row["user_id"] != user_id:
            raise PrefError("игра не найдена")
        if row["status"] != "active":
            raise PrefError("игра уже закончена")
        st = json.loads(row["state"])
        bet = row["bet"]
        frames = []
        if action == "surrender":
            st["phase"] = "over"
            st["result"] = "lose"
            st["note"] = "Ты сдался"
        else:
            r = apply_human(st, action, bid, card, cards, contract)
            frames.append(public(st, gid, bet))
            if r == "trick":
                _resolve_trick(st)
                frames.append(public(st, gid, bet))
            _run_bots(st, gid, bet, frames)
        if st["result"]:
            fin = _settle(conn, row, st)
            final = public(st, gid, bet, fin)
            if frames:
                frames[-1] = final
            else:
                frames.append(final)
        else:
            cur = conn.execute(
                "UPDATE pref_games SET state = ?, ver = ver + 1 WHERE id = ? AND status = 'active' AND ver = ?",
                (json.dumps(st), gid, row["ver"]),
            )
            if cur.rowcount == 0:
                raise PrefError("игра уже изменилась, обнови")
    out = dict(frames[-1])
    out["frames"] = frames
    return out


# ---------------------------------------------------------------------------
# FastAPI
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
    bid: str | None = None
    card: str | None = None
    cards: list[str] | None = None
    contract: str | None = None


def register(app, authenticate) -> None:
    init_table()

    @app.post("/api/pref/start")
    def pref_start(body: _StartBody):
        user = authenticate(body.initData)
        try:
            return start(user["telegram_id"], body.bet)
        except PrefError as e:
            raise HTTPException(400, str(e))
        except db.InsufficientGems:
            raise HTTPException(400, "not enough gems")

    @app.post("/api/pref/active")
    def pref_active(body: _InitBody):
        user = authenticate(body.initData)
        return {"game": active(user["telegram_id"])}

    @app.post("/api/pref/act")
    def pref_act(body: _ActBody):
        user = authenticate(body.initData)
        try:
            return act(user["telegram_id"], body.game_id, body.action, body.bid, body.card, body.cards, body.contract)
        except PrefError as e:
            raise HTTPException(400, str(e))
