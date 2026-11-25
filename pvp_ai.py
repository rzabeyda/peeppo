"""«Бот» -- игровой соперник в PvP. Живёт внутри процесса peeppo_bot (одна лёгкая
asyncio-задача, запросы к SQLite раз в TICK_SECONDS).

Что делает:
  * Как только в PvP открыт пустой раунд (никто ещё не поставил), Бот первым ставит
    случайное количество своих свободных карт. Игроки заходят и играют против него:
    второй участник запускает обычный отсчёт PVP_LOCK_SECONDS, победитель по весам.
  * Карты Бот получает только через обычный фарм (database.farm -- те же шансы, что у
    всех): раз в FARM_INTERVAL_SECONDS одно нажатие, и дофармливает, если на ставку
    не хватает карт. Сколько ставить -- по истории живых игроков (min..max, чаще медиана).
    Кристаллы на фарм ему докладываются ровно на одно нажатие (25 💎).
  * Раунды, у которых истёк отсчёт, тоже закрывает (resolve_due_pvp_rounds), чтобы
    игра доигрывалась, даже если игрок закрыл приложение.
Ничего никому не пишет (BOT_PUSH_MESSAGES не трогается).
"""
import asyncio
import logging
import random
import time

import database as db

logger = logging.getLogger("pvp_ai")

PVP_AI_ENABLED = True
PVP_AI_ID = -2               # отрицательный -- не пересечётся с реальным Telegram id (-1 занят /pvpbot)
PVP_AI_NAME = "Бот"
TICK_SECONDS = 15
FARM_INTERVAL_SECONDS = 15 * 60
# Сколько карт ставить -- считается по истории живых игроков (player_stake_stats):
STATS_ROUNDS = 300           # по скольким последним раундам
FARM_BURST = 10              # максимум нажатий фарма за один тик, когда не хватает карт
STAKE_CAP = 30              # потолок на всякий случай, даже если кто-то ставил больше
FALLBACK_MIN, FALLBACK_MAX = 1, 5  # если истории ещё нет

_last_farm = 0.0


def _ensure_user():
    with db.get_conn() as conn:
        row = conn.execute("SELECT 1 FROM users WHERE telegram_id = ?", (PVP_AI_ID,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users (telegram_id, username, first_name, gems, created_at) VALUES (?, NULL, ?, 0, ?)",
                (PVP_AI_ID, PVP_AI_NAME, db._now()),
            )
            logger.info("pvp_ai: создан пользователь %s (%s)", PVP_AI_ID, PVP_AI_NAME)


def _farm_once():
    global _last_farm
    _last_farm = time.monotonic()
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE users SET gems = MAX(gems, ?) WHERE telegram_id = ?", (db.FARM_COST_GEMS, PVP_AI_ID)
        )
    res = db.farm(PVP_AI_ID)
    if res and not res.get("miss"):
        logger.info("pvp_ai: нафармил %s (%s)", res.get("name"), res.get("rarity"))


def _free_cards() -> list[int]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM user_cards uc WHERE user_id = ? AND voided = 0 AND listed_price IS NULL "
            "AND swap_listed = 0 AND staked_at IS NULL AND pvp_round_id IS NULL AND pinned_at IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM number_giveaways ng WHERE ng.user_card_id = uc.id AND ng.drawn_at IS NULL)",
            (PVP_AI_ID,),
        ).fetchall()
    return [r["id"] for r in rows]


def _round_to_join() -> int | None:
    """id открытого раунда, если Бота в нём ещё нет и отсчёт не идёт (раунд пустой или
    игрок сидит там один и ждёт соперника). Если уже играют двое+ -- не лезет."""
    with db.get_conn() as conn:
        round_id = db._get_or_create_open_round(conn)
        lock = conn.execute("SELECT lock_at FROM pvp_rounds WHERE id = ?", (round_id,)).fetchone()["lock_at"]
        mine = conn.execute(
            "SELECT 1 FROM pvp_entries WHERE round_id = ? AND user_id = ? LIMIT 1", (round_id, PVP_AI_ID)
        ).fetchone()
    return round_id if (lock is None and mine is None) else None


def player_stake_stats(last_rounds: int = STATS_ROUNDS) -> dict | None:
    """Сколько карт за раунд ставят живые игроки (по последним last_rounds раундам):
    min / max / среднее / медиана, плюс то же по «весу» (ценности) ставки."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) AS n, SUM(pe.weight) AS w FROM pvp_entries pe "
            "WHERE pe.user_id > 0 AND pe.round_id IN "
            "(SELECT id FROM pvp_rounds WHERE status = 'resolved' ORDER BY id DESC LIMIT ?) "
            "GROUP BY pe.round_id, pe.user_id",
            (last_rounds,),
        ).fetchall()
    if not rows:
        return None
    counts = sorted(r["n"] for r in rows)
    weights = sorted(r["w"] for r in rows)
    return {
        "stakes": len(counts),
        "min": counts[0], "max": counts[-1],
        "avg": round(sum(counts) / len(counts), 1), "median": counts[len(counts) // 2],
        "w_min": weights[0], "w_max": weights[-1],
        "w_avg": round(sum(weights) / len(weights), 1), "w_median": weights[len(weights) // 2],
    }


_stats_cache = (0.0, None)


def _stake_count() -> int:
    """Случайно, но в пределах того, как ставят люди: треугольное распределение
    min..max с пиком на медиане (обычно ближе к среднему, изредка много)."""
    global _stats_cache
    if time.monotonic() - _stats_cache[0] > 3600 or _stats_cache[1] is None:
        _stats_cache = (time.monotonic(), player_stake_stats())
    s = _stats_cache[1]
    if not s:
        return random.randint(FALLBACK_MIN, FALLBACK_MAX)
    hi = min(s["max"], STAKE_CAP)
    lo = min(s["min"], hi)
    mode = min(max(s["median"], lo), hi)
    return max(1, round(random.triangular(lo, hi, mode)))


def tick():
    db.resolve_due_pvp_rounds()
    if time.monotonic() - _last_farm >= FARM_INTERVAL_SECONDS:
        _farm_once()
    round_id = _round_to_join()
    if round_id is None:
        return
    want = _stake_count()
    free = _free_cards()
    # Карт меньше, чем хочет поставить -- дофармливает (обычные шансы, промахи тоже бывают),
    # не больше FARM_BURST нажатий за тик.
    for _ in range(min(FARM_BURST, want - len(free))):
        _farm_once()
    free = _free_cards()
    if not free:
        return
    n = min(want, len(free))
    picked = random.sample(free, n)
    try:
        db.join_pvp_round(PVP_AI_ID, picked, round_id)
        logger.info("pvp_ai: поставил %d карт в раунд %s", n, round_id)
    except (db.PvpCardNotOwned, db.PvpRoundLocked):
        pass  # игрок успел первым / раунд сменился -- попробуем на следующем тике


async def pvp_ai_loop():
    if not PVP_AI_ENABLED:
        return
    try:
        _ensure_user()
    except Exception:
        logger.exception("pvp_ai: не смог создать пользователя")
        return
    # Первое нажатие фарма -- через интервал; на старте фармит только если ставить нечего.
    global _last_farm
    _last_farm = time.monotonic()
    while True:
        try:
            tick()
        except Exception:
            logger.exception("pvp_ai: ошибка в тике")
        await asyncio.sleep(TICK_SECONDS)
