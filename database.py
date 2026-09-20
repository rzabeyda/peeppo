"""
Peeppo — database layer (SQLite, sync sqlite3, WAL mode for bot+api concurrent access).

Tables:
  users       — telegram profile + who invited them
  cards       — catalog of collectible images (filename lives in static/cards/<filename>)
                populated later by hand (INSERT rows once real images are dropped in)
  user_cards  — inventory rows, one per farm drop; duplicates allowed, no supply cap

Design notes (per project decisions):
  - Farm has no cooldown/energy — every tap grants a card immediately.
  - Card supply is unlimited — any card can drop to any number of users, dupes allowed.
  - All game state lives here (server-side) — Telegram WebApp has no localStorage.
"""

import sqlite3
import random
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "peeppo.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id   INTEGER PRIMARY KEY,
    username      TEXT,
    first_name    TEXT,
    photo_url     TEXT,
    ref_by        INTEGER REFERENCES users(telegram_id),
    gems          INTEGER NOT NULL DEFAULT 0,
    gems_earned   INTEGER NOT NULL DEFAULT 0,
    last_daily_bonus TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cards (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    filename      TEXT NOT NULL,
    name          TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1,
    rarity        TEXT NOT NULL DEFAULT 'silver',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_cards (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL REFERENCES users(telegram_id),
    card_id           INTEGER NOT NULL REFERENCES cards(id),
    obtained_at       TEXT NOT NULL,
    transfer_pending  INTEGER NOT NULL DEFAULT 0,
    listed_price      INTEGER,
    swap_listed       INTEGER NOT NULL DEFAULT 0,
    staked_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_user_cards_user ON user_cards(user_id);

CREATE TABLE IF NOT EXISTS market_offers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_card_id  INTEGER NOT NULL REFERENCES user_cards(id),
    buyer_id      INTEGER NOT NULL REFERENCES users(telegram_id),
    price_gems    INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_offers_card ON market_offers(user_card_id);

CREATE TABLE IF NOT EXISTS swap_offers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_card_id  INTEGER NOT NULL REFERENCES user_cards(id),
    buyer_id      INTEGER NOT NULL REFERENCES users(telegram_id),
    status        TEXT NOT NULL DEFAULT 'pending',
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_swap_offers_card ON swap_offers(user_card_id);

CREATE TABLE IF NOT EXISTS swap_offer_cards (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    swap_offer_id  INTEGER NOT NULL REFERENCES swap_offers(id),
    user_card_id   INTEGER NOT NULL REFERENCES user_cards(id)
);

CREATE INDEX IF NOT EXISTS idx_swap_offer_cards_offer ON swap_offer_cards(swap_offer_id);

CREATE TABLE IF NOT EXISTS pvp_rounds (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    status        TEXT NOT NULL DEFAULT 'open',   -- 'open' | 'resolved'
    lock_at       TEXT,                            -- set once the 2nd distinct player joins; round resolves 60s later
    winner_id     INTEGER REFERENCES users(telegram_id),
    created_at    TEXT NOT NULL,
    resolved_at   TEXT
);

CREATE TABLE IF NOT EXISTS pvp_entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id      INTEGER NOT NULL REFERENCES pvp_rounds(id),
    user_id       INTEGER NOT NULL REFERENCES users(telegram_id),
    user_card_id  INTEGER NOT NULL REFERENCES user_cards(id),
    rarity        TEXT NOT NULL,
    weight        INTEGER NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pvp_entries_round ON pvp_entries(round_id);

CREATE TABLE IF NOT EXISTS gem_drops (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    amount        INTEGER NOT NULL,
    claimed_by    INTEGER REFERENCES users(telegram_id),
    claimed_at    TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS giveaways (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    amount         INTEGER NOT NULL,
    winners_count  INTEGER NOT NULL,
    created_at     TEXT NOT NULL,
    draw_at        TEXT NOT NULL,
    drawn_at       TEXT,
    message_id     INTEGER
);

CREATE TABLE IF NOT EXISTS giveaway_entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    giveaway_id   INTEGER NOT NULL REFERENCES giveaways(id),
    user_id       INTEGER NOT NULL REFERENCES users(telegram_id),
    joined_at     TEXT NOT NULL,
    UNIQUE(giveaway_id, user_id)
);

CREATE TABLE IF NOT EXISTS hundred_club (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    drawn_at      TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # migration for DBs created before photo_url existed
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "photo_url" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN photo_url TEXT")
        # migration for DBs created before transfer_pending existed
        uc_cols = {row["name"] for row in conn.execute("PRAGMA table_info(user_cards)")}
        if "transfer_pending" not in uc_cols:
            conn.execute("ALTER TABLE user_cards ADD COLUMN transfer_pending INTEGER NOT NULL DEFAULT 0")
        if "listed_price" not in uc_cols:
            conn.execute("ALTER TABLE user_cards ADD COLUMN listed_price INTEGER")
        if "swap_listed" not in uc_cols:
            conn.execute("ALTER TABLE user_cards ADD COLUMN swap_listed INTEGER NOT NULL DEFAULT 0")
        # migration for DBs created before gems existed
        u_cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "gems" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN gems INTEGER NOT NULL DEFAULT 0")
        # migration for DBs created before gems_earned existed (lifetime gems earned, for the Топы screen)
        if "gems_earned" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN gems_earned INTEGER NOT NULL DEFAULT 0")
        # migration for DBs created before staking existed
        if "staked_at" not in uc_cols:
            conn.execute("ALTER TABLE user_cards ADD COLUMN staked_at TEXT")
        # migration for DBs created before card rarity existed
        c_cols = {row["name"] for row in conn.execute("PRAGMA table_info(cards)")}
        if "rarity" not in c_cols:
            conn.execute("ALTER TABLE cards ADD COLUMN rarity TEXT NOT NULL DEFAULT 'silver'")
            conn.execute("UPDATE users SET gems_earned = gems WHERE gems_earned = 0")
        # migration for DBs created before the daily login bonus existed
        if "last_daily_bonus" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN last_daily_bonus TEXT")
        # migration for DBs created before PvP existed
        if "pvp_round_id" not in uc_cols:
            conn.execute("ALTER TABLE user_cards ADD COLUMN pvp_round_id INTEGER REFERENCES pvp_rounds(id)")
        # migration for DBs created before deferred (anti-bot) referral payout existed
        if "ref_reward_pending" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN ref_reward_pending INTEGER NOT NULL DEFAULT 0")
        # migration for DBs created before the PvP result reveal existed. Telegram
        # WebApp has no localStorage, so "has this player already seen this round's
        # result" has to live server-side, per player, not in the browser.
        if "pvp_last_seen_round_id" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN pvp_last_seen_round_id INTEGER")
        # migration for the in-app (not bot-DM) referral reward popup — holds the
        # referred player's display name until the referrer's client next calls
        # /api/auth, which shows it once and clears it (same read-once pattern as
        # the PvP reveal, since there's nowhere client-side to remember it either).
        if "referral_reward_notice" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN referral_reward_notice TEXT")
        # migration for the trade-history feature — market/swap trades previously
        # weren't logged at all once completed (only current ownership was kept), so
        # there was nothing to show in a "История" list. seller_id/completed_at (or
        # accepted_at) are frozen at trade time since the underlying card's owner
        # keeps changing hands afterwards.
        mo_cols = {row["name"] for row in conn.execute("PRAGMA table_info(market_offers)")}
        if "seller_id" not in mo_cols:
            conn.execute("ALTER TABLE market_offers ADD COLUMN seller_id INTEGER REFERENCES users(telegram_id)")
        if "completed_at" not in mo_cols:
            conn.execute("ALTER TABLE market_offers ADD COLUMN completed_at TEXT")
        so_cols = {row["name"] for row in conn.execute("PRAGMA table_info(swap_offers)")}
        if "seller_id" not in so_cols:
            conn.execute("ALTER TABLE swap_offers ADD COLUMN seller_id INTEGER REFERENCES users(telegram_id)")
        if "accepted_at" not in so_cols:
            conn.execute("ALTER TABLE swap_offers ADD COLUMN accepted_at TEXT")
        # migration for the daily fortune wheel — once-per-day spin, tracked the same
        # way as last_daily_bonus (UTC calendar day).
        if "last_wheel_spin" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN last_wheel_spin TEXT")


DAILY_BONUS_GEMS = 25
REFERRAL_REWARD_GEMS = 25
MAX_REWARDED_REFERRALS = 10  # after this many, referrals still count but stop paying out
SIGNUP_BONUS_GEMS = 50
EARLY_SIGNUP_BONUS_GEMS = 100
EARLY_SIGNUP_LIMIT = 100  # the first 100 users ever to register get EARLY_SIGNUP_BONUS_GEMS
                          # instead of SIGNUP_BONUS_GEMS (checked against the users count
                          # at the moment they register, so it's a hard cutoff at 100 total)


def get_or_create_user(telegram_id: int, username: str | None, first_name: str | None,
                        photo_url: str | None = None, ref_by: int | None = None) -> tuple[sqlite3.Row, bool]:
    """Returns (row, is_new) — is_new is True only the very first time this telegram_id
    is seen, so callers can fire a one-time "new user" notification off of it. The
    returned row's ref_by reflects whatever was actually accepted (None if the referrer
    didn't exist or was the same person) — callers can check it to know whether a
    referral reward was actually credited just now."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if row:
            # keep profile fields fresh, never overwrite an existing ref_by
            conn.execute(
                "UPDATE users SET username = ?, first_name = ?, photo_url = ? WHERE telegram_id = ?",
                (username, first_name, photo_url, telegram_id),
            )
            return conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone(), False

        # don't let someone set themselves as their own referrer
        if ref_by == telegram_id:
            ref_by = None
        # referrer must already exist, otherwise ignore the payload silently
        prior_referrals = 0
        if ref_by is not None:
            exists = conn.execute("SELECT 1 FROM users WHERE telegram_id = ?", (ref_by,)).fetchone()
            if not exists:
                ref_by = None
            else:
                prior_referrals = conn.execute(
                    "SELECT COUNT(*) AS n FROM users WHERE ref_by = ?", (ref_by,)
                ).fetchone()["n"]

        today = datetime.now(timezone.utc).date().isoformat()
        # Anti-bot: don't pay the referrer yet, just for opening the bot — that's free
        # for anyone to fake. Flag it pending; farm() pays out once this new player makes
        # their first real farm, proving there's an actual person behind the account.
        # Still capped at their first MAX_REWARDED_REFERRALS invites, same as before —
        # referrals beyond that still count (get_referral_count keeps growing) but never
        # flip this flag, so they simply never pay out.
        ref_reward_pending = 1 if (ref_by is not None and prior_referrals < MAX_REWARDED_REFERRALS) else 0
        total_users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        signup_bonus = EARLY_SIGNUP_BONUS_GEMS if total_users < EARLY_SIGNUP_LIMIT else SIGNUP_BONUS_GEMS
        conn.execute(
            "INSERT INTO users (telegram_id, username, first_name, photo_url, ref_by, gems, gems_earned, "
            "last_daily_bonus, created_at, ref_reward_pending) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (telegram_id, username, first_name, photo_url, ref_by, signup_bonus, signup_bonus, today, _now(), ref_reward_pending),
        )
        return conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone(), True


def get_user(telegram_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()


def claim_daily_bonus(user_id: int) -> int:
    """Credits DAILY_BONUS_GEMS once per calendar day (UTC) the user opens the app —
    returns the amount credited (0 if they already claimed today, or the signup day,
    since new users already get SIGNUP_BONUS_GEMS and last_daily_bonus is pre-set
    to that day in get_or_create_user)."""
    today = datetime.now(timezone.utc).date().isoformat()
    with get_conn() as conn:
        row = conn.execute("SELECT last_daily_bonus FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if row is None or row["last_daily_bonus"] == today:
            return 0
        conn.execute(
            "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ?, last_daily_bonus = ? WHERE telegram_id = ?",
            (DAILY_BONUS_GEMS, DAILY_BONUS_GEMS, today, user_id),
        )
        return DAILY_BONUS_GEMS


# ---------------------------------------------------------------------------
# Daily fortune wheel — once per calendar day (UTC), the client shows a spinning
# wheel the moment the user opens the app. The odds are intentionally never sent
# to the client (only the outcome), so the frontend's wheel segments don't have to
# match the real probabilities.
# ---------------------------------------------------------------------------

WHEEL_CHANCE_50 = 0.01   # 1% — win 50 gems
WHEEL_CHANCE_25 = 0.10   # 10% — win 25 gems
                          # (implicit ~89% chance of winning nothing)


def wheel_available(user_id: int) -> bool:
    """Whether this user still has today's (UTC) fortune-wheel spin unused."""
    today = datetime.now(timezone.utc).date().isoformat()
    with get_conn() as conn:
        row = conn.execute("SELECT last_wheel_spin FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        return row is not None and row["last_wheel_spin"] != today


def spin_fortune_wheel(user_id: int) -> dict:
    """Rolls today's spin (server-side only — the odds never leave this function),
    credits any winnings, and marks the spin used for today. Returns {'ok': True,
    'amount': 0|25|50} normally, or {'ok': False} if this user already spun today
    (a stale/duplicate client call)."""
    today = datetime.now(timezone.utc).date().isoformat()
    with get_conn() as conn:
        row = conn.execute("SELECT last_wheel_spin FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if row is None or row["last_wheel_spin"] == today:
            return {"ok": False, "amount": 0}
        roll = random.random()
        if roll < WHEEL_CHANCE_50:
            amount = 50
        elif roll < WHEEL_CHANCE_50 + WHEEL_CHANCE_25:
            amount = 25
        else:
            amount = 0
        if amount:
            conn.execute(
                "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ?, last_wheel_spin = ? "
                "WHERE telegram_id = ?",
                (amount, amount, today, user_id),
            )
        else:
            conn.execute("UPDATE users SET last_wheel_spin = ? WHERE telegram_id = ?", (today, user_id))
        return {"ok": True, "amount": amount}


def get_users_missing_daily_bonus() -> list[int]:
    """Telegram ids of every user who has NOT yet claimed today's (UTC) daily bonus —
    used by daily_reminder.py to nudge them with a bot message."""
    today = datetime.now(timezone.utc).date().isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT telegram_id FROM users WHERE last_daily_bonus IS NULL OR last_daily_bonus != ?",
            (today,),
        ).fetchall()
        return [r["telegram_id"] for r in rows]


def add_card_to_catalog(filename: str, name: str | None = None) -> int:
    """Register one image file in the catalog. Call this once per image you drop into static/cards/."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO cards (filename, name, is_active, created_at) VALUES (?, ?, 1, ?)",
            (filename, name, _now()),
        )
        return cur.lastrowid


def get_all_cards() -> list[dict]:
    """Full active catalog, alphabetical by name — used by the 'Модели' gallery
    (so it reads as a sorted list) and the farm animation's cycling preview."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id AS card_id, filename, name, rarity FROM cards WHERE is_active = 1 ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]


# Tiers: silver (was rare) < gold (was epic) < platinum (was legend) < diamond (new top tier).
# Tiers: bronze (junk/memes, sub-$100) < silver ($100-1k) < gold ($1k-10k) < platinum ($10k-100k) < diamond (>$100k).
RARITY_WEIGHTS = {"bronze": 50, "silver": 29, "gold": 13, "platinum": 7, "diamond": 1}


def draw_random_card() -> sqlite3.Row | None:
    """Pick one active card, weighted by rarity (RARITY_WEIGHTS) — most drops are BRONZE,
    then SILVER, GOLD and PLATINUM progressively less common. Returns None if the catalog is empty.
    Falls back gracefully (equal weight) if a tier has no active cards yet."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM cards WHERE is_active = 1").fetchall()
        if not rows:
            return None
        by_rarity: dict[str, list] = {}
        for r in rows:
            by_rarity.setdefault(r["rarity"] or "silver", []).append(r)
        tiers = list(by_rarity.keys())
        weights = [RARITY_WEIGHTS.get(t, 1) for t in tiers]
        chosen_tier = random.choices(tiers, weights=weights, k=1)[0]
        return random.choice(by_rarity[chosen_tier])


def grant_card(user_id: int, card_id: int) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO user_cards (user_id, card_id, obtained_at) VALUES (?, ?, ?)",
            (user_id, card_id, _now()),
        )
        return cur.lastrowid


FARM_COST_GEMS = 25


class InsufficientGems(Exception):
    """Raised by farm() when the user's balance is below FARM_COST_GEMS."""


def farm(user_id: int) -> dict | None:
    """Draw a random card in exchange for FARM_COST_GEMS and grant it to the user, all in
    one transaction. Returns the granted card, or None if the catalog is empty. Raises
    InsufficientGems if the balance check fails (checked and deducted atomically, so two
    farms fired in quick succession can't both spend the same last few gems)."""
    card = draw_random_card()
    if card is None:
        return None
    with get_conn() as conn:
        row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if row is None or row["gems"] < FARM_COST_GEMS:
            raise InsufficientGems()
        prior_cards = conn.execute(
            "SELECT COUNT(*) FROM user_cards WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (FARM_COST_GEMS, user_id))
        cur = conn.execute(
            "INSERT INTO user_cards (user_id, card_id, obtained_at) VALUES (?, ?, ?)",
            (user_id, card["id"], _now()),
        )
        user_card_id = cur.lastrowid
        # Global drop number — position among ALL cards ever farmed/crafted by ANY user,
        # not just this user's own collection.
        drop_number = conn.execute("SELECT COUNT(*) FROM user_cards").fetchone()[0]

        # Anti-bot referral payout: only on this player's very first-ever farm, and only
        # once (ref_reward_pending is cleared right after), so a pending referral reward
        # can never fire twice.
        referral_reward = None
        if prior_cards == 0:
            me = conn.execute(
                "SELECT ref_by, ref_reward_pending FROM users WHERE telegram_id = ?", (user_id,)
            ).fetchone()
            if me["ref_by"] is not None and me["ref_reward_pending"]:
                conn.execute(
                    "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? WHERE telegram_id = ?",
                    (REFERRAL_REWARD_GEMS, REFERRAL_REWARD_GEMS, me["ref_by"]),
                )
                conn.execute("UPDATE users SET ref_reward_pending = 0 WHERE telegram_id = ?", (user_id,))
                referral_reward = {"referrer_id": me["ref_by"]}

    result = {
        "user_card_id": user_card_id,
        "card_id": card["id"],
        "filename": card["filename"],
        "name": card["name"],
        "rarity": card["rarity"],
        "drop_number": drop_number,
    }
    if referral_reward:
        result["referral_reward"] = referral_reward
    return result


CRAFT_COST_GEMS = 25
TRANSFER_FEE_GEMS = 3  # charged to the sender for a direct @username gift
MIN_LISTING_PRICE_GEMS = 20  # floor for both a market listing price and a buyer's offer

# Craft: burn one owned card + CRAFT_COST_GEMS gems for one new random card. Odds depend on
# the tier of the card being burned — burning a higher tier gives much better odds at another
# high tier (mirrors most collector-game "upgrade" mechanics). Each row must sum to 100.
CRAFT_WEIGHTS = {
    # Crafting never produces a tier BELOW the one burned — only same-tier-or-higher is
    # possible (burn Gold, get Gold/Platinum/Diamond only, never Bronze/Silver). Each row
    # only lists the tiers it can actually produce and must sum to 100.
    "bronze":   {"bronze": 50, "silver": 35, "gold": 12, "platinum": 2.5, "diamond": 0.5},
    "silver":   {"silver": 55, "gold": 35, "platinum": 8, "diamond": 2},
    "gold":     {"gold": 55, "platinum": 35, "diamond": 10},
    "platinum": {"platinum": 65, "diamond": 35},
    # No "diamond" entry — Diamond is the top tier already, it can't be crafted away
    # (see the check in craft_card() below, which raises CraftNotAllowed for it).
}


class CraftNotOwned(Exception):
    """Raised by craft_card() when user_card_id doesn't exist or doesn't belong to this user."""


class CraftNotAllowed(Exception):
    """Raised by craft_card() when trying to craft a Diamond-tier card — it's already the
    top tier, so it can't be burned for a chance at something else."""


def draw_card_for_craft(input_rarity: str) -> sqlite3.Row | None:
    """Like draw_random_card(), but weighted by CRAFT_WEIGHTS[input_rarity] instead of the
    normal farm odds. Falls back to the bronze table for an unrecognized input tier."""
    weights_for_tier = CRAFT_WEIGHTS.get(input_rarity, CRAFT_WEIGHTS["bronze"])
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM cards WHERE is_active = 1").fetchall()
        if not rows:
            return None
        by_rarity: dict[str, list] = {}
        for r in rows:
            by_rarity.setdefault(r["rarity"] or "silver", []).append(r)
        tiers = list(by_rarity.keys())
        # 0 (not 0.1) for any tier missing from this input tier's row — that's exactly
        # how lower tiers get excluded now that each row only lists same-or-higher tiers.
        weights = [weights_for_tier.get(t, 0) for t in tiers]
        chosen_tier = random.choices(tiers, weights=weights, k=1)[0]
        return random.choice(by_rarity[chosen_tier])


def craft_card(user_id: int, user_card_id: int) -> dict:
    """Burns one owned card (must belong to user_id) for CRAFT_COST_GEMS gems and one new
    random card, drawn with odds skewed by the burned card's rarity. The new card is inserted
    with the exact same obtained_at as the burned one, so it inherits its collection number
    (drop_number) instead of jumping to the end of the list. Raises InsufficientGems if the
    gem balance is too low, CraftNotOwned if user_card_id isn't this user's."""
    with get_conn() as conn:
        owned = conn.execute(
            "SELECT uc.id, uc.obtained_at, uc.listed_price, uc.swap_listed, uc.staked_at, uc.pvp_round_id, c.rarity "
            "FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
            "WHERE uc.id = ? AND uc.user_id = ?",
            (user_card_id, user_id),
        ).fetchone()
        if owned is None:
            raise CraftNotOwned()
        if owned["listed_price"] is not None or owned["swap_listed"] or owned["staked_at"] is not None or owned["pvp_round_id"] is not None:
            raise CraftNotOwned()
        if (owned["rarity"] or "bronze") == "diamond":
            raise CraftNotAllowed()
        gems_row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if gems_row is None or gems_row["gems"] < CRAFT_COST_GEMS:
            raise InsufficientGems()

    new_card = draw_card_for_craft(owned["rarity"] or "bronze")
    if new_card is None:
        raise ValueError("catalog is empty")

    with get_conn() as conn:
        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (CRAFT_COST_GEMS, user_id))
        # Reuse the same user_cards row (swap its card_id in place) instead of deleting it
        # and inserting a fresh one. market_offers, swap_offers, swap_offer_cards, and
        # pvp_entries all carry a FOREIGN KEY on user_cards.id for history purposes, so a
        # card that had ever been offered, swapped, or staked in the past (even in a long-
        # finished deal) made the old DELETE blow up with "FOREIGN KEY constraint failed" —
        # updating in place keeps the same id (every old reference stays valid) while still
        # fully replacing which card it is.
        conn.execute(
            "UPDATE user_cards SET card_id = ?, listed_price = NULL, swap_listed = 0, staked_at = NULL, pvp_round_id = NULL WHERE id = ?",
            (new_card["id"], user_card_id),
        )
        new_user_card_id = user_card_id
        # Global drop number — same rule as farm(): position among ALL cards ever
        # farmed/crafted by ANY user, not just this user's own collection.
        drop_number = conn.execute(
            "SELECT COUNT(*) FROM user_cards WHERE obtained_at <= ?",
            (owned["obtained_at"],),
        ).fetchone()[0]

    return {
        "user_card_id": new_user_card_id,
        "card_id": new_card["id"],
        "filename": new_card["filename"],
        "name": new_card["name"],
        "rarity": new_card["rarity"],
        "drop_number": drop_number,
    }


def get_inventory(user_id: int) -> list[dict]:
    """Cards the user owns — one row per copy, shown separately even when duplicated
    (duplicates are common since supply is unlimited). drop_number is a GLOBAL rank —
    position among every card ever farmed/crafted by any user, ordered by obtained_at —
    not a per-user counter."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT uc.id AS user_card_id, c.id AS card_id, c.filename, c.name, c.rarity,
                   uc.listed_price, uc.swap_listed, uc.staked_at, uc.pvp_round_id,
                   (SELECT COUNT(*) FROM user_cards uc2 WHERE uc2.obtained_at <= uc.obtained_at) AS drop_number
            FROM user_cards uc
            JOIN cards c ON c.id = uc.card_id
            WHERE uc.user_id = ?
            ORDER BY uc.obtained_at DESC
            """,
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_user_card(user_card_id: int) -> sqlite3.Row | None:
    """A single inventory row (used by /api/share to look up which file+owner to send)."""
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT uc.id, uc.user_id, uc.card_id, uc.obtained_at, uc.transfer_pending, c.filename, c.name
            FROM user_cards uc JOIN cards c ON c.id = uc.card_id
            WHERE uc.id = ?
            """,
            (user_card_id,),
        ).fetchone()


def start_transfer(user_card_id: int, owner_id: int) -> bool:
    """Owner marks one specific owned copy as giveable. Returns False if they don't own it."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id FROM user_cards WHERE id = ?", (user_card_id,)
        ).fetchone()
        if row is None or row["user_id"] != owner_id:
            return False
        conn.execute("UPDATE user_cards SET transfer_pending = 1 WHERE id = ?", (user_card_id,))
        return True


def claim_transfer(user_card_id: int, new_owner_id: int) -> dict | None:
    """
    A friend opens the claim link and taps /start — this moves ownership over.
    Returns {from_user_id, filename, name} on success, or None if the link is invalid,
    already used, or the claimer is the original owner (no self-gifting).
    """
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT uc.user_id AS from_user_id, uc.transfer_pending, c.filename, c.name
            FROM user_cards uc JOIN cards c ON c.id = uc.card_id
            WHERE uc.id = ?
            """,
            (user_card_id,),
        ).fetchone()
        if row is None or not row["transfer_pending"] or row["from_user_id"] == new_owner_id:
            return None
        conn.execute(
            "UPDATE user_cards SET user_id = ?, transfer_pending = 0 WHERE id = ?",
            (new_owner_id, user_card_id),
        )
        return {"from_user_id": row["from_user_id"], "filename": row["filename"], "name": row["name"]}


def get_gems(user_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        return row["gems"] if row else 0


def create_gem_drop(amount: int) -> int:
    """Admin's /gem command (or the auto-scheduler) in the public chat: creates a
    first-come-first-served gem drop and returns its id (embedded in the claim
    button's callback_data)."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO gem_drops (amount, created_at) VALUES (?, ?)", (amount, _now())
        )
        return cur.lastrowid


def get_last_gem_drop_time() -> str | None:
    """UTC ISO timestamp of the most recently created gem drop (manual or automatic),
    or None if there has never been one. Used by the auto-scheduler to avoid firing a
    second drop too soon after a bot restart."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT created_at FROM gem_drops ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["created_at"] if row else None


def claim_gem_drop(drop_id: int, user_id: int) -> dict:
    """Atomic first-come-first-served claim — the UPDATE ... WHERE claimed_by IS NULL
    is the actual race gate; only one concurrent caller can ever win it, regardless of
    how many people tap the button at the same instant. Returns {'ok': True, 'amount'}
    for whoever wins, or {'ok': False, 'amount', 'claimed_by_name'} for everyone else
    (including a second tap by the winner themselves)."""
    with get_conn() as conn:
        drop = conn.execute(
            "SELECT amount, claimed_by FROM gem_drops WHERE id = ?", (drop_id,)
        ).fetchone()
        if drop is None:
            return {"ok": False, "amount": None, "claimed_by_name": None}
        cur = conn.execute(
            "UPDATE gem_drops SET claimed_by = ?, claimed_at = ? WHERE id = ? AND claimed_by IS NULL",
            (user_id, _now(), drop_id),
        )
        if cur.rowcount == 1:
            conn.execute(
                "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? WHERE telegram_id = ?",
                (drop["amount"], drop["amount"], user_id),
            )
            return {"ok": True, "amount": drop["amount"]}
        current = conn.execute(
            "SELECT g.amount, u.username, u.first_name FROM gem_drops g "
            "LEFT JOIN users u ON u.telegram_id = g.claimed_by WHERE g.id = ?",
            (drop_id,),
        ).fetchone()
        claimer_name = None
        if current is not None:
            claimer_name = (
                f"@{current['username']}" if current["username"]
                else (current["first_name"] or "игрок")
            )
        return {
            "ok": False,
            "amount": current["amount"] if current is not None else drop["amount"],
            "claimed_by_name": claimer_name,
        }


# ---------------------------------------------------------------------------
# Channel giveaways — admin posts a "Розыгрыш" in the channel with a deep-link
# button; anyone who taps it and opens the bot is entered (new or existing
# players alike); after a fixed delay the bot auto-draws winners_count random
# entrants and credits each `amount` gems.
# ---------------------------------------------------------------------------

def create_giveaway(amount: int, winners_count: int, duration_hours: float) -> int:
    """Admin's /giveaway command: creates a giveaway that auto-draws after
    duration_hours and returns its id (embedded in the entry deep-link)."""
    draw_at = (datetime.now(timezone.utc) + timedelta(hours=duration_hours)).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO giveaways (amount, winners_count, created_at, draw_at) VALUES (?, ?, ?, ?)",
            (amount, winners_count, _now(), draw_at),
        )
        return cur.lastrowid


def set_giveaway_message(giveaway_id: int, message_id: int):
    """Remembers the channel post's message_id so the auto-draw can edit that same
    message with the results instead of posting a separate one."""
    with get_conn() as conn:
        conn.execute("UPDATE giveaways SET message_id = ? WHERE id = ?", (message_id, giveaway_id))


def join_giveaway(giveaway_id: int, user_id: int) -> str:
    """Enters user_id into the giveaway — anyone who clicks the link counts, new
    registration or existing player alike. Returns 'joined', 'already_joined',
    'drawn' (too late, already picked) or 'not_found'."""
    with get_conn() as conn:
        giveaway = conn.execute(
            "SELECT drawn_at FROM giveaways WHERE id = ?", (giveaway_id,)
        ).fetchone()
        if giveaway is None:
            return "not_found"
        if giveaway["drawn_at"] is not None:
            return "drawn"
        existing = conn.execute(
            "SELECT 1 FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?",
            (giveaway_id, user_id),
        ).fetchone()
        if existing:
            return "already_joined"
        conn.execute(
            "INSERT INTO giveaway_entries (giveaway_id, user_id, joined_at) VALUES (?, ?, ?)",
            (giveaway_id, user_id, _now()),
        )
        return "joined"


def get_due_giveaways() -> list[dict]:
    """Giveaways whose draw time has passed but that haven't been drawn yet — polled
    by the bot's giveaway scheduler."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM giveaways WHERE drawn_at IS NULL AND draw_at <= ?", (_now(),)
        ).fetchall()
        return [dict(row) for row in rows]


def draw_giveaway(giveaway_id: int) -> dict:
    """Randomly picks up to winners_count entrants (all of them if there are fewer),
    credits each `amount` gems, marks the giveaway drawn, and returns
    {'amount', 'winners': [{'telegram_id','username','first_name'}, ...], 'total_entries'}."""
    with get_conn() as conn:
        giveaway = conn.execute("SELECT * FROM giveaways WHERE id = ?", (giveaway_id,)).fetchone()
        entries = [
            dict(row) for row in conn.execute(
                "SELECT u.telegram_id, u.username, u.first_name FROM giveaway_entries ge "
                "JOIN users u ON u.telegram_id = ge.user_id WHERE ge.giveaway_id = ?",
                (giveaway_id,),
            ).fetchall()
        ]
        winners = random.sample(entries, k=min(len(entries), giveaway["winners_count"])) if entries else []
        for w in winners:
            conn.execute(
                "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? WHERE telegram_id = ?",
                (giveaway["amount"], giveaway["amount"], w["telegram_id"]),
            )
        conn.execute("UPDATE giveaways SET drawn_at = ? WHERE id = ?", (_now(), giveaway_id))
        return {"amount": giveaway["amount"], "winners": winners, "total_entries": len(entries)}


# ---------------------------------------------------------------------------
# "Hundred club" — a one-off contest that fires by itself exactly once, the moment
# the 100th player ever registers (or immediately on first check after deploy, if
# there are already 100+): draws 10 random winners from the first 100 registered
# players and credits each 200 gems. The hundred_club table's single row is just
# an idempotency guard so this can never fire twice.
# ---------------------------------------------------------------------------

HUNDRED_CLUB_SIZE = 100
HUNDRED_CLUB_WINNERS = 10
HUNDRED_CLUB_AMOUNT = 200


def maybe_run_hundred_club_contest() -> dict | None:
    """Safe to call as often as you like (on every new registration, on every bot
    restart, whatever) — a no-op once it has already fired, or until there are
    HUNDRED_CLUB_SIZE registered users. Returns {'amount', 'winners': [...],
    'total_entrants'} the one time it actually draws, otherwise None."""
    with get_conn() as conn:
        already = conn.execute("SELECT 1 FROM hundred_club LIMIT 1").fetchone()
        if already:
            return None
        total = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        if total < HUNDRED_CLUB_SIZE:
            return None
        entrants = [
            dict(row) for row in conn.execute(
                "SELECT telegram_id, username, first_name FROM users ORDER BY created_at ASC LIMIT ?",
                (HUNDRED_CLUB_SIZE,),
            ).fetchall()
        ]
        winners = random.sample(entrants, k=min(len(entrants), HUNDRED_CLUB_WINNERS))
        for w in winners:
            conn.execute(
                "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? WHERE telegram_id = ?",
                (HUNDRED_CLUB_AMOUNT, HUNDRED_CLUB_AMOUNT, w["telegram_id"]),
            )
        conn.execute("INSERT INTO hundred_club (drawn_at) VALUES (?)", (_now(),))
        return {"amount": HUNDRED_CLUB_AMOUNT, "winners": winners, "total_entrants": len(entrants)}


def add_gems(user_id: int, amount: int) -> int:
    """Credits (or, with a negative amount, debits) gems. Returns the new balance."""
    with get_conn() as conn:
        conn.execute("UPDATE users SET gems = gems + ? WHERE telegram_id = ?", (amount, user_id))
        if amount > 0:
            conn.execute("UPDATE users SET gems_earned = gems_earned + ? WHERE telegram_id = ?", (amount, user_id))
        row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        return row["gems"]


def list_card(user_card_id: int, seller_id: int, price_gems: int) -> bool:
    """Owner puts one specific owned copy up for sale (or re-prices an existing listing).
    Blocked while that same copy is already listed for swap — a card can only be in one
    kind of lot at a time (list_for_swap enforces the same rule in the other direction).
    Also blocked while the copy is staked — it has to be pulled out of staking first."""
    if price_gems < MIN_LISTING_PRICE_GEMS:
        return False
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id, swap_listed, staked_at, pvp_round_id FROM user_cards WHERE id = ?", (user_card_id,)
        ).fetchone()
        if row is None or row["user_id"] != seller_id:
            return False
        if row["swap_listed"] or row["staked_at"] is not None or row["pvp_round_id"] is not None:
            return False
        conn.execute("UPDATE user_cards SET listed_price = ? WHERE id = ?", (price_gems, user_card_id))
        return True


def unlist_card(user_card_id: int, seller_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT user_id FROM user_cards WHERE id = ?", (user_card_id,)).fetchone()
        if row is None or row["user_id"] != seller_id:
            return False
        conn.execute("UPDATE user_cards SET listed_price = NULL WHERE id = ?", (user_card_id,))
        conn.execute(
            "UPDATE market_offers SET status = 'cancelled' WHERE user_card_id = ? AND status = 'pending'",
            (user_card_id,),
        )
        return True


def get_market_listings() -> list[dict]:
    """All cards currently for sale, newest first."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT uc.id AS user_card_id, uc.listed_price, uc.user_id AS seller_id,
                   c.id AS card_id, c.filename, c.name, c.rarity, u.username, u.first_name
            FROM user_cards uc
            JOIN cards c ON c.id = uc.card_id
            JOIN users u ON u.telegram_id = uc.user_id
            WHERE uc.listed_price IS NOT NULL
            ORDER BY uc.id DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def buy_listing(user_card_id: int, buyer_id: int) -> dict | None:
    """Instant buy at the seller's asking price. Returns deal details, or None if the
    listing is gone, the buyer is the seller, or the buyer can't afford it."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT uc.user_id AS seller_id, uc.listed_price, c.filename, c.name
            FROM user_cards uc JOIN cards c ON c.id = uc.card_id
            WHERE uc.id = ?
            """,
            (user_card_id,),
        ).fetchone()
        if row is None or row["listed_price"] is None or row["seller_id"] == buyer_id:
            return None
        price = row["listed_price"]
        buyer = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (buyer_id,)).fetchone()
        if buyer is None or buyer["gems"] < price:
            return None
        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (price, buyer_id))
        conn.execute("UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? WHERE telegram_id = ?", (price, price, row["seller_id"]))
        conn.execute(
            "UPDATE user_cards SET user_id = ?, listed_price = NULL WHERE id = ?",
            (buyer_id, user_card_id),
        )
        conn.execute(
            "UPDATE market_offers SET status = 'cancelled' WHERE user_card_id = ? AND status = 'pending'",
            (user_card_id,),
        )
        # Instant buys never otherwise touch market_offers — log one directly (as
        # already-'accepted') so the История view has a record of it, same as a
        # negotiated offer does when accepted below.
        conn.execute(
            "INSERT INTO market_offers (user_card_id, buyer_id, price_gems, status, seller_id, created_at, completed_at) "
            "VALUES (?, ?, ?, 'accepted', ?, ?, ?)",
            (user_card_id, buyer_id, price, row["seller_id"], _now(), _now()),
        )
        return {"seller_id": row["seller_id"], "price": price, "filename": row["filename"], "name": row["name"]}


def make_offer(user_card_id: int, buyer_id: int, price_gems: int) -> dict | None:
    """Buyer proposes their own price on a listed card. Seller must accept it (via bot DM)
    before anything changes hands. Returns offer + card/seller info for the notification,
    or None if the listing doesn't exist / isn't for sale / buyer == seller."""
    if price_gems < MIN_LISTING_PRICE_GEMS:
        return None
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT uc.user_id AS seller_id, uc.listed_price, c.filename, c.name
            FROM user_cards uc JOIN cards c ON c.id = uc.card_id
            WHERE uc.id = ?
            """,
            (user_card_id,),
        ).fetchone()
        if row is None or row["listed_price"] is None or row["seller_id"] == buyer_id:
            return None
        cur = conn.execute(
            "INSERT INTO market_offers (user_card_id, buyer_id, price_gems, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
            (user_card_id, buyer_id, price_gems, _now()),
        )
        return {
            "offer_id": cur.lastrowid,
            "seller_id": row["seller_id"],
            "filename": row["filename"],
            "name": row["name"],
        }


def get_offer(offer_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT mo.id, mo.user_card_id, mo.buyer_id, mo.price_gems, mo.status,
                   uc.user_id AS seller_id, uc.listed_price, c.filename, c.name
            FROM market_offers mo
            JOIN user_cards uc ON uc.id = mo.user_card_id
            JOIN cards c ON c.id = uc.card_id
            WHERE mo.id = ?
            """,
            (offer_id,),
        ).fetchone()


def accept_offer(offer_id: int, seller_id: int) -> dict | None:
    """Seller accepts a pending offer from their bot DM. Executes the trade at the offer's
    price and cancels any other pending offers on that same card. Returns deal details,
    or None if the offer is gone/not pending/not this seller's, or the buyer can no longer afford it."""
    with get_conn() as conn:
        offer = conn.execute(
            """
            SELECT mo.id, mo.user_card_id, mo.buyer_id, mo.price_gems, mo.status, uc.user_id AS seller_id
            FROM market_offers mo JOIN user_cards uc ON uc.id = mo.user_card_id
            WHERE mo.id = ?
            """,
            (offer_id,),
        ).fetchone()
        if offer is None or offer["status"] != "pending" or offer["seller_id"] != seller_id:
            return None
        buyer = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (offer["buyer_id"],)).fetchone()
        if buyer is None or buyer["gems"] < offer["price_gems"]:
            conn.execute("UPDATE market_offers SET status = 'expired' WHERE id = ?", (offer_id,))
            return None
        card = conn.execute(
            "SELECT filename, name FROM cards c JOIN user_cards uc ON uc.card_id = c.id WHERE uc.id = ?",
            (offer["user_card_id"],),
        ).fetchone()
        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (offer["price_gems"], offer["buyer_id"]))
        conn.execute("UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? WHERE telegram_id = ?", (offer["price_gems"], offer["price_gems"], seller_id))
        conn.execute(
            "UPDATE user_cards SET user_id = ?, listed_price = NULL WHERE id = ?",
            (offer["buyer_id"], offer["user_card_id"]),
        )
        conn.execute(
            "UPDATE market_offers SET status = 'accepted', seller_id = ?, completed_at = ? WHERE id = ?",
            (seller_id, _now(), offer_id),
        )
        conn.execute(
            "UPDATE market_offers SET status = 'cancelled' WHERE user_card_id = ? AND status = 'pending' AND id != ?",
            (offer["user_card_id"], offer_id),
        )
        return {
            "buyer_id": offer["buyer_id"],
            "price": offer["price_gems"],
            "filename": card["filename"],
            "name": card["name"],
        }


def decline_offer(offer_id: int, seller_id: int) -> dict | None:
    with get_conn() as conn:
        offer = conn.execute(
            """
            SELECT mo.id, mo.buyer_id, mo.status, uc.user_id AS seller_id, c.name
            FROM market_offers mo
            JOIN user_cards uc ON uc.id = mo.user_card_id
            JOIN cards c ON c.id = uc.card_id
            WHERE mo.id = ?
            """,
            (offer_id,),
        ).fetchone()
        if offer is None or offer["status"] != "pending" or offer["seller_id"] != seller_id:
            return None
        conn.execute("UPDATE market_offers SET status = 'declined' WHERE id = ?", (offer_id,))
        return {"buyer_id": offer["buyer_id"], "name": offer["name"]}


# ---------------------------------------------------------------------------
# Swap / barter market — card-for-card(s), no gems involved
# ---------------------------------------------------------------------------

def list_for_swap(user_card_id: int, seller_id: int) -> bool:
    """Owner marks one specific owned copy as available to swap. A card can't be
    listed for swap while it's also listed for gems sale (or already up for swap).
    Also blocked while the copy is staked — it has to be pulled out of staking first."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id, listed_price, swap_listed, staked_at, pvp_round_id FROM user_cards WHERE id = ?", (user_card_id,)
        ).fetchone()
        if row is None or row["user_id"] != seller_id:
            return False
        if row["listed_price"] is not None or row["swap_listed"] or row["staked_at"] is not None or row["pvp_round_id"] is not None:
            return False
        conn.execute("UPDATE user_cards SET swap_listed = 1 WHERE id = ?", (user_card_id,))
        return True


def unlist_swap(user_card_id: int, seller_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT user_id FROM user_cards WHERE id = ?", (user_card_id,)).fetchone()
        if row is None or row["user_id"] != seller_id:
            return False
        conn.execute("UPDATE user_cards SET swap_listed = 0 WHERE id = ?", (user_card_id,))
        conn.execute(
            "UPDATE swap_offers SET status = 'cancelled' WHERE user_card_id = ? AND status = 'pending'",
            (user_card_id,),
        )
        return True


def get_swap_listings() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT uc.id AS user_card_id, uc.user_id AS seller_id, c.id AS card_id, c.filename, c.name, c.rarity
            FROM user_cards uc
            JOIN cards c ON c.id = uc.card_id
            WHERE uc.swap_listed = 1
            ORDER BY uc.id DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


# Daily staking payout, per the staked card's own rarity — also charged as the one-time
# upfront fee to start staking that same card (a Gold card costs 25 gems to stake, then
# earns 25 gems/day going forward).
STAKE_DAILY_RATES = {"bronze": 5, "silver": 10, "gold": 25, "platinum": 50, "diamond": 100}
STAKE_PERIOD_SECONDS = 24 * 60 * 60


def settle_staking(user_id: int) -> int:
    """Credits STAKE_DAILY_RATES[rarity] gems for every full 24h period elapsed since
    each of the user's staked cards was staked (or last paid out), advancing that card's
    clock forward by exactly that many whole days so partial progress toward the next
    payout is never lost or double-paid. Called from _authenticate() on every API request
    so staking pays out passively with no background job. Returns gems credited this call."""
    now = datetime.now(timezone.utc)
    total_credited = 0
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT uc.id, uc.staked_at, c.rarity FROM user_cards uc "
            "JOIN cards c ON c.id = uc.card_id "
            "WHERE uc.user_id = ? AND uc.staked_at IS NOT NULL",
            (user_id,),
        ).fetchall()
        for row in rows:
            staked_at = datetime.fromisoformat(row["staked_at"])
            elapsed = (now - staked_at).total_seconds()
            full_days = int(elapsed // STAKE_PERIOD_SECONDS)
            if full_days >= 1:
                new_staked_at = staked_at + timedelta(seconds=full_days * STAKE_PERIOD_SECONDS)
                conn.execute(
                    "UPDATE user_cards SET staked_at = ? WHERE id = ?",
                    (new_staked_at.isoformat(), row["id"]),
                )
                daily_rate = STAKE_DAILY_RATES.get(row["rarity"] or "bronze", 5)
                total_credited += full_days * daily_rate
        if total_credited:
            conn.execute(
                "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? WHERE telegram_id = ?",
                (total_credited, total_credited, user_id),
            )
    return total_credited


def stake_card(user_card_id: int, owner_id: int) -> bool:
    """Puts one owned copy into staking, after charging an upfront fee equal to that
    card's own daily staking rate (STAKE_DAILY_RATES). Blocked if it's already staked, or
    currently listed for sale/swap (unlist first). Raises InsufficientGems if the fee
    can't be covered."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT uc.user_id, uc.listed_price, uc.swap_listed, uc.staked_at, uc.pvp_round_id, c.rarity "
            "FROM user_cards uc JOIN cards c ON c.id = uc.card_id WHERE uc.id = ?",
            (user_card_id,),
        ).fetchone()
        if row is None or row["user_id"] != owner_id:
            return False
        if row["listed_price"] is not None or row["swap_listed"] or row["staked_at"] is not None or row["pvp_round_id"] is not None:
            return False
        fee = STAKE_DAILY_RATES.get(row["rarity"] or "bronze", 5)
        gems_row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (owner_id,)).fetchone()
        if gems_row is None or gems_row["gems"] < fee:
            raise InsufficientGems()
        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (fee, owner_id))
        conn.execute("UPDATE user_cards SET staked_at = ? WHERE id = ?", (_now(), user_card_id))
        return True


def unstake_card(user_card_id: int, owner_id: int) -> bool:
    """Pulls one copy out of staking. Settles (and pays out) any completed full days
    first — whatever's left of the current, still-incomplete day is simply dropped,
    same as leaving early on a bank term deposit."""
    settle_staking(owner_id)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id, staked_at FROM user_cards WHERE id = ?", (user_card_id,)
        ).fetchone()
        if row is None or row["user_id"] != owner_id or row["staked_at"] is None:
            return False
        conn.execute("UPDATE user_cards SET staked_at = NULL WHERE id = ?", (user_card_id,))
        return True


def propose_swap(user_card_id: int, buyer_id: int, offered_user_card_ids: list[int]) -> dict | None:
    """Buyer offers one or more of their own cards in exchange for a swap-listed card.
    Nothing changes hands yet — the seller accepts or declines from their bot DM."""
    if not offered_user_card_ids:
        return None
    with get_conn() as conn:
        listing = conn.execute(
            """
            SELECT uc.user_id AS seller_id, uc.swap_listed, c.filename, c.name
            FROM user_cards uc JOIN cards c ON c.id = uc.card_id
            WHERE uc.id = ?
            """,
            (user_card_id,),
        ).fetchone()
        if listing is None or not listing["swap_listed"] or listing["seller_id"] == buyer_id:
            return None

        offered_names = []
        for oid in offered_user_card_ids:
            row = conn.execute(
                """
                SELECT uc.user_id, uc.listed_price, uc.swap_listed, uc.staked_at, uc.pvp_round_id, c.name
                FROM user_cards uc JOIN cards c ON c.id = uc.card_id
                WHERE uc.id = ?
                """,
                (oid,),
            ).fetchone()
            if row is None or row["user_id"] != buyer_id:
                return None  # buyer doesn't actually own one of the offered cards
            if (row["listed_price"] is not None or row["swap_listed"] or row["staked_at"] is not None
                    or row["pvp_round_id"] is not None):
                return None  # offered card is busy — on sale, already in another swap, or staked in PvP
            offered_names.append(row["name"] or "картинка")

        cur = conn.execute(
            "INSERT INTO swap_offers (user_card_id, buyer_id, status, created_at) VALUES (?, ?, 'pending', ?)",
            (user_card_id, buyer_id, _now()),
        )
        offer_id = cur.lastrowid
        for oid in offered_user_card_ids:
            conn.execute(
                "INSERT INTO swap_offer_cards (swap_offer_id, user_card_id) VALUES (?, ?)",
                (offer_id, oid),
            )
        return {
            "offer_id": offer_id,
            "seller_id": listing["seller_id"],
            "listing_name": listing["name"],
            "listing_filename": listing["filename"],
            "offered_names": offered_names,
        }


def accept_swap_offer(offer_id: int, seller_id: int) -> dict | None:
    """Executes the trade: the listed card moves to the buyer, every offered card
    moves to the seller. Returns None if anything about the deal is no longer valid
    (offer gone, cards moved elsewhere in the meantime, etc)."""
    with get_conn() as conn:
        offer = conn.execute(
            """
            SELECT so.id, so.user_card_id, so.buyer_id, so.status, uc.user_id AS seller_id, c.name
            FROM swap_offers so
            JOIN user_cards uc ON uc.id = so.user_card_id
            JOIN cards c ON c.id = uc.card_id
            WHERE so.id = ?
            """,
            (offer_id,),
        ).fetchone()
        if offer is None or offer["status"] != "pending" or offer["seller_id"] != seller_id:
            return None

        offered_rows = conn.execute(
            "SELECT user_card_id FROM swap_offer_cards WHERE swap_offer_id = ?", (offer_id,)
        ).fetchall()
        offered_ids = [r["user_card_id"] for r in offered_rows]

        # re-verify the buyer still owns every offered card, and that none of them got
        # listed for sale/swap or staked in PvP after the offer was proposed
        for oid in offered_ids:
            row = conn.execute(
                "SELECT user_id, listed_price, swap_listed, staked_at, pvp_round_id FROM user_cards WHERE id = ?",
                (oid,),
            ).fetchone()
            if row is None or row["user_id"] != offer["buyer_id"]:
                conn.execute("UPDATE swap_offers SET status = 'expired' WHERE id = ?", (offer_id,))
                return None
            if (row["listed_price"] is not None or row["swap_listed"] or row["staked_at"] is not None
                    or row["pvp_round_id"] is not None):
                conn.execute("UPDATE swap_offers SET status = 'expired' WHERE id = ?", (offer_id,))
                return None

        # also re-verify the listed card itself hasn't been pulled out of swap in the meantime
        listing_row = conn.execute(
            "SELECT swap_listed, user_id FROM user_cards WHERE id = ?", (offer["user_card_id"],)
        ).fetchone()
        if listing_row is None or not listing_row["swap_listed"] or listing_row["user_id"] != seller_id:
            conn.execute("UPDATE swap_offers SET status = 'expired' WHERE id = ?", (offer_id,))
            return None

        conn.execute(
            "UPDATE user_cards SET user_id = ?, swap_listed = 0 WHERE id = ?",
            (offer["buyer_id"], offer["user_card_id"]),
        )
        for oid in offered_ids:
            conn.execute(
                "UPDATE user_cards SET user_id = ?, swap_listed = 0 WHERE id = ?",
                (seller_id, oid),
            )
        conn.execute(
            "UPDATE swap_offers SET status = 'accepted', seller_id = ?, accepted_at = ? WHERE id = ?",
            (seller_id, _now(), offer_id),
        )
        conn.execute(
            "UPDATE swap_offers SET status = 'cancelled' WHERE user_card_id = ? AND status = 'pending' AND id != ?",
            (offer["user_card_id"], offer_id),
        )
        return {"buyer_id": offer["buyer_id"], "name": offer["name"]}


def decline_swap_offer(offer_id: int, seller_id: int) -> dict | None:
    with get_conn() as conn:
        offer = conn.execute(
            """
            SELECT so.id, so.buyer_id, so.status, uc.user_id AS seller_id, c.name
            FROM swap_offers so
            JOIN user_cards uc ON uc.id = so.user_card_id
            JOIN cards c ON c.id = uc.card_id
            WHERE so.id = ?
            """,
            (offer_id,),
        ).fetchone()
        if offer is None or offer["status"] != "pending" or offer["seller_id"] != seller_id:
            return None
        conn.execute("UPDATE swap_offers SET status = 'declined' WHERE id = ?", (offer_id,))
        return {"buyer_id": offer["buyer_id"], "name": offer["name"]}


def find_user_by_username(username: str) -> sqlite3.Row | None:
    """Looks up a user by their Telegram @username (case-insensitive, no leading @).
    Only finds people who have opened the bot at least once — Telegram's Bot API has
    no way to resolve an arbitrary username to an id otherwise."""
    username = username.lstrip("@")
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (username,)
        ).fetchone()


def transfer_card_to(user_card_id: int, from_user_id: int, to_user_id: int) -> dict | None:
    """Direct gift by username — no claim link needed since we already know the recipient.
    Charges the sender TRANSFER_FEE_GEMS gems upfront. Returns {filename, name} on success,
    or None if the sender doesn't own the card, is trying to gift it to themselves, or the
    card is currently busy (listed for sale/swap, staked, or in a PvP round). Raises
    InsufficientGems if the sender can't cover the fee."""
    if from_user_id == to_user_id:
        return None
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT uc.user_id, uc.listed_price, uc.swap_listed, uc.staked_at, uc.pvp_round_id, c.filename, c.name
            FROM user_cards uc JOIN cards c ON c.id = uc.card_id
            WHERE uc.id = ?
            """,
            (user_card_id,),
        ).fetchone()
        if row is None or row["user_id"] != from_user_id:
            return None
        if row["listed_price"] is not None or row["swap_listed"] or row["staked_at"] is not None or row["pvp_round_id"] is not None:
            return None
        gems_row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (from_user_id,)).fetchone()
        if gems_row is None or gems_row["gems"] < TRANSFER_FEE_GEMS:
            raise InsufficientGems()
        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (TRANSFER_FEE_GEMS, from_user_id))
        conn.execute(
            "UPDATE user_cards SET user_id = ?, listed_price = NULL, transfer_pending = 0 WHERE id = ?",
            (to_user_id, user_card_id),
        )
        conn.execute(
            "UPDATE market_offers SET status = 'cancelled' WHERE user_card_id = ? AND status = 'pending'",
            (user_card_id,),
        )
        return {"filename": row["filename"], "name": row["name"]}


def get_referral_count(user_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users WHERE ref_by = ?", (user_id,)).fetchone()
        return row["n"]


def set_referral_notice(referrer_id: int, who_name: str) -> None:
    """Called from /api/farm the moment a referral reward is credited — stores the
    referred player's display name so the referrer's own client can pop an in-app
    'you got 25 gems' modal next time it loads, instead of a bot DM."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET referral_reward_notice = ? WHERE telegram_id = ?",
            (who_name, referrer_id),
        )


def get_and_clear_referral_notice(user_id: int) -> str | None:
    """Read-once: returns the pending referral-reward name (if any) and clears it in
    the same call, so /api/auth shows the popup exactly once per reward."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT referral_reward_notice FROM users WHERE telegram_id = ?", (user_id,)
        ).fetchone()
        notice = row["referral_reward_notice"] if row else None
        if notice:
            conn.execute(
                "UPDATE users SET referral_reward_notice = NULL WHERE telegram_id = ?", (user_id,)
            )
        return notice


def get_total_farmed() -> int:
    """Global count of every farm drop ever, across all users — also doubles as the
    highest drop number handed out so far, since user_cards.id is a plain AUTOINCREMENT."""
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM user_cards").fetchone()
        return row["n"]


def get_leaderboard() -> list[dict]:
    """Players ranked by total cards owned and by lifetime gems earned — for the 'Топы'
    screen, which lets the player switch between the two rankings client-side. Unlike
    the market, identities are shown here on purpose: that's the whole point of a
    leaderboard. Returns everyone (no LIMIT) since the two rankings can surface different
    people; the frontend slices each to its own top 50."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT u.telegram_id, u.username, u.first_name, u.photo_url,
                   COUNT(uc.id) AS total_cards, u.gems_earned
            FROM users u
            LEFT JOIN user_cards uc ON uc.user_id = u.telegram_id
            WHERE LOWER(u.username) IS NOT '@rzabeyda' AND LOWER(u.username) IS NOT 'rzabeyda'
            GROUP BY u.telegram_id
            ORDER BY total_cards DESC, u.telegram_id ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]



# ---------------------------------------------------------------------------
# Trade history ("История") — market_offers/swap_offers rows already carry every
# completed trade once accepted (buy_listing/accept_market_offer/accept_swap_offer
# stamp seller_id + a completion timestamp on them, see init_db's migration) — these
# just read them back out, newest first, for the market/swap "История" panel.
# ---------------------------------------------------------------------------

def get_market_history(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT mo.price_gems, mo.completed_at,
                   seller.username AS seller_username, seller.first_name AS seller_first_name,
                   buyer.username AS buyer_username, buyer.first_name AS buyer_first_name,
                   c.filename, c.name, c.rarity
            FROM market_offers mo
            JOIN user_cards uc ON uc.id = mo.user_card_id
            JOIN cards c ON c.id = uc.card_id
            JOIN users buyer ON buyer.telegram_id = mo.buyer_id
            LEFT JOIN users seller ON seller.telegram_id = mo.seller_id
            WHERE mo.status = 'accepted'
            ORDER BY mo.completed_at DESC, mo.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "price_gems": r["price_gems"],
            "completed_at": r["completed_at"],
            "seller_name": (f"@{r['seller_username']}" if r["seller_username"] else (r["seller_first_name"] or "игрок")),
            "buyer_name": (f"@{r['buyer_username']}" if r["buyer_username"] else (r["buyer_first_name"] or "игрок")),
            "filename": r["filename"], "name": r["name"], "rarity": r["rarity"],
        })
    return out


def get_swap_history(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        offers = conn.execute(
            """
            SELECT so.id, so.accepted_at,
                   seller.username AS seller_username, seller.first_name AS seller_first_name,
                   buyer.username AS buyer_username, buyer.first_name AS buyer_first_name,
                   c.filename, c.name, c.rarity
            FROM swap_offers so
            JOIN user_cards uc ON uc.id = so.user_card_id
            JOIN cards c ON c.id = uc.card_id
            JOIN users buyer ON buyer.telegram_id = so.buyer_id
            LEFT JOIN users seller ON seller.telegram_id = so.seller_id
            WHERE so.status = 'accepted'
            ORDER BY so.accepted_at DESC, so.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out = []
        for o in offers:
            offered = conn.execute(
                """
                SELECT c.filename, c.name, c.rarity
                FROM swap_offer_cards soc
                JOIN user_cards uc ON uc.id = soc.user_card_id
                JOIN cards c ON c.id = uc.card_id
                WHERE soc.swap_offer_id = ?
                """,
                (o["id"],),
            ).fetchall()
            out.append({
                "accepted_at": o["accepted_at"],
                "seller_name": (f"@{o['seller_username']}" if o["seller_username"] else (o["seller_first_name"] or "игрок")),
                "buyer_name": (f"@{o['buyer_username']}" if o["buyer_username"] else (o["buyer_first_name"] or "игрок")),
                "listed_card": {"filename": o["filename"], "name": o["name"], "rarity": o["rarity"]},
                "offered_cards": [{"filename": c["filename"], "name": c["name"], "rarity": c["rarity"]} for c in offered],
            })
    return out


# ---------------------------------------------------------------------------
# PvP jackpot — players stake any number of their own unlocked cards into one shared,
# global lobby round. Once a 2nd distinct player joins, a 60s countdown starts (others
# can still join during it); when it elapses, ONE winner is drawn at random, weighted by
# the total rarity-weight of everyone's staked cards, and takes every staked card from
# every participant. Cards can't be pulled back out once staked. Resolution is checked
# opportunistically from api.py (on /api/pvp/state and /api/auth) rather than a cron job
# — same no-background-job philosophy as settle_staking() — since it only matters while
# someone is actually looking at the PvP screen.
# ---------------------------------------------------------------------------

PVP_LOCK_SECONDS = 60
# Staking closes this many seconds before the round actually resolves, so a card can't
# be thrown in right at the wire (matches the frontend disabling the stake button once
# the on-screen countdown reaches this threshold).
PVP_JOIN_CUTOFF_SECONDS = 5
# Rarity -> "power" in the pot. Mirrors real-world value tiers (bronze junk vs.
# diamond-grade), not farm drop odds — a single Diamond outweighs many Bronze cards.
PVP_RARITY_WEIGHTS = {"bronze": 1, "silver": 3, "gold": 8, "platinum": 20, "diamond": 50}


class PvpCardNotOwned(Exception):
    """Raised by join_pvp_round() when a user_card_id isn't the caller's, or is already
    tied up (listed for sale/swap, staked, or already in a PvP round)."""


class PvpRoundLocked(Exception):
    """Raised by join_pvp_round() when the current round is within PVP_JOIN_CUTOFF_SECONDS
    of resolving (or has already resolved) — it's not accepting new entries any more."""


def _get_or_create_open_round(conn) -> int:
    row = conn.execute("SELECT id FROM pvp_rounds WHERE status = 'open' ORDER BY id DESC LIMIT 1").fetchone()
    if row is not None:
        return row["id"]
    cur = conn.execute("INSERT INTO pvp_rounds (status, created_at) VALUES ('open', ?)", (_now(),))
    return cur.lastrowid


def join_pvp_round(user_id: int, user_card_ids: list[int]) -> dict:
    """Stakes one or more of the caller's own cards into the current open PvP round
    (joining it if it's their first entry this round, or topping up if they already
    have). Starts the 60s countdown the moment a 2nd distinct player has any card in
    the round. Raises PvpCardNotOwned if a card isn't theirs or is already busy,
    PvpRoundLocked if the round's countdown has already elapsed."""
    if not user_card_ids:
        raise PvpCardNotOwned()
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        round_id = _get_or_create_open_round(conn)
        round_row = conn.execute("SELECT lock_at FROM pvp_rounds WHERE id = ?", (round_id,)).fetchone()
        if round_row["lock_at"] is not None:
            seconds_left = (datetime.fromisoformat(round_row["lock_at"]) - now).total_seconds()
            if seconds_left <= PVP_JOIN_CUTOFF_SECONDS:
                raise PvpRoundLocked()

        for ucid in user_card_ids:
            row = conn.execute(
                "SELECT user_id, listed_price, swap_listed, staked_at, pvp_round_id, card_id "
                "FROM user_cards WHERE id = ?",
                (ucid,),
            ).fetchone()
            if row is None or row["user_id"] != user_id:
                raise PvpCardNotOwned()
            if (row["listed_price"] is not None or row["swap_listed"] or row["staked_at"] is not None
                    or row["pvp_round_id"] is not None):
                raise PvpCardNotOwned()
            card_row = conn.execute("SELECT rarity FROM cards WHERE id = ?", (row["card_id"],)).fetchone()
            rarity = (card_row["rarity"] if card_row else None) or "bronze"
            weight = PVP_RARITY_WEIGHTS.get(rarity, 1)
            conn.execute("UPDATE user_cards SET pvp_round_id = ? WHERE id = ?", (round_id, ucid))
            conn.execute(
                "INSERT INTO pvp_entries (round_id, user_id, user_card_id, rarity, weight, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (round_id, user_id, ucid, rarity, weight, _now()),
            )

        distinct_players = conn.execute(
            "SELECT COUNT(DISTINCT user_id) AS n FROM pvp_entries WHERE round_id = ?", (round_id,)
        ).fetchone()["n"]
        if distinct_players >= 2 and round_row["lock_at"] is None:
            lock_at = (now + timedelta(seconds=PVP_LOCK_SECONDS)).isoformat()
            conn.execute("UPDATE pvp_rounds SET lock_at = ? WHERE id = ?", (lock_at, round_id))

    return get_pvp_state(user_id)


def get_pvp_state(viewer_id: int | None = None) -> dict:
    """Current open lobby round: countdown, every participant's staked cards, and each
    participant's live win chance (their total weight / everyone's total weight)."""
    with get_conn() as conn:
        round_id = _get_or_create_open_round(conn)
        round_row = conn.execute("SELECT id, lock_at FROM pvp_rounds WHERE id = ?", (round_id,)).fetchone()
        entries = conn.execute(
            """
            SELECT pe.user_id, u.username, u.first_name, pe.user_card_id, pe.rarity, pe.weight,
                   c.filename, c.name
            FROM pvp_entries pe
            JOIN users u ON u.telegram_id = pe.user_id
            JOIN user_cards uc ON uc.id = pe.user_card_id
            JOIN cards c ON c.id = uc.card_id
            WHERE pe.round_id = ?
            ORDER BY pe.id ASC
            """,
            (round_id,),
        ).fetchall()

    by_user: dict[int, dict] = {}
    total_weight = 0
    for e in entries:
        total_weight += e["weight"]
        bucket = by_user.setdefault(e["user_id"], {
            "user_id": e["user_id"],
            "username": e["username"],
            "first_name": e["first_name"],
            "total_weight": 0,
            "cards": [],
        })
        bucket["total_weight"] += e["weight"]
        bucket["cards"].append({
            "user_card_id": e["user_card_id"],
            "filename": e["filename"],
            "name": e["name"],
            "rarity": e["rarity"],
        })

    participants = sorted(by_user.values(), key=lambda p: -p["total_weight"])
    for p in participants:
        p["chance_pct"] = round(p["total_weight"] / total_weight * 100, 1) if total_weight else 0

    lock_at = round_row["lock_at"]
    seconds_left = None
    if lock_at is not None:
        seconds_left = max(0, int((datetime.fromisoformat(lock_at) - datetime.now(timezone.utc)).total_seconds()))

    last_result = get_last_resolved_pvp_round()
    unseen_result = None
    if viewer_id is not None and last_result is not None:
        with get_conn() as conn:
            seen_row = conn.execute(
                "SELECT pvp_last_seen_round_id FROM users WHERE telegram_id = ?", (viewer_id,)
            ).fetchone()
            already_seen = seen_row is not None and seen_row["pvp_last_seen_round_id"] == last_result["round_id"]
            if not already_seen:
                unseen_result = last_result
                conn.execute(
                    "UPDATE users SET pvp_last_seen_round_id = ? WHERE telegram_id = ?",
                    (last_result["round_id"], viewer_id),
                )

    return {
        "round_id": round_row["id"],
        "seconds_left": seconds_left,
        "total_weight": total_weight,
        "participants": participants,
        "you_joined": bool(viewer_id is not None and viewer_id in by_user),
        "last_result": last_result,       # always included — for the persistent banner
        "unseen_result": unseen_result,   # only the first time THIS viewer sees it — for the popup
    }


def get_last_resolved_pvp_round() -> dict | None:
    """The most recently resolved PvP round's summary — so the lobby can show a 'last
    result' banner even to players who weren't watching when it happened (this is the
    only place the actual winner reveal lives; the client shows it once per round_id).
    Includes a full `participants` breakdown (same shape as get_pvp_state's), rebuilt
    from the historical pvp_entries rows, so the client can redraw the exact wheel the
    round was decided on for the spin-to-a-winner reveal animation."""
    with get_conn() as conn:
        round_row = conn.execute(
            "SELECT id, winner_id, resolved_at FROM pvp_rounds WHERE status = 'resolved' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if round_row is None or round_row["winner_id"] is None:
            return None
        winner = conn.execute(
            "SELECT username, first_name FROM users WHERE telegram_id = ?", (round_row["winner_id"],)
        ).fetchone()
        entries = conn.execute(
            """
            SELECT pe.user_id, u.username, u.first_name, pe.weight, c.filename, c.name
            FROM pvp_entries pe
            JOIN users u ON u.telegram_id = pe.user_id
            JOIN user_cards uc ON uc.id = pe.user_card_id
            JOIN cards c ON c.id = uc.card_id
            WHERE pe.round_id = ?
            ORDER BY pe.id ASC
            """,
            (round_row["id"],),
        ).fetchall()

    by_user: dict[int, dict] = {}
    total_weight = 0
    for e in entries:
        total_weight += e["weight"]
        bucket = by_user.setdefault(e["user_id"], {
            "user_id": e["user_id"],
            "username": e["username"],
            "first_name": e["first_name"],
            "total_weight": 0,
            "cards": [],
        })
        bucket["total_weight"] += e["weight"]
        bucket["cards"].append({"filename": e["filename"], "name": e["name"]})
    participants = sorted(by_user.values(), key=lambda p: -p["total_weight"])
    for p in participants:
        p["chance_pct"] = round(p["total_weight"] / total_weight * 100, 1) if total_weight else 0

    if winner is not None and winner["username"]:
        winner_name = winner["username"]
    elif winner is not None and winner["first_name"]:
        winner_name = winner["first_name"]
    else:
        winner_name = "игрок"
    return {
        "round_id": round_row["id"],
        "winner_id": round_row["winner_id"],
        "winner_name": winner_name,
        "total_cards": len(entries),
        "total_players": len(participants),
        "participants": participants,
    }


def get_pvp_history(limit: int = 50) -> list[dict]:
    """Every resolved PvP round, newest first — summary rows only (winner, cards/players
    count, timestamp), for the PvP "История" panel. The one-time celebratory reveal
    animation still uses get_last_resolved_pvp_round()'s full participant breakdown;
    this is just the compact log of everything that's ever happened."""
    with get_conn() as conn:
        rounds = conn.execute(
            """
            SELECT pr.id, pr.winner_id, pr.resolved_at, w.username AS winner_username, w.first_name AS winner_first_name
            FROM pvp_rounds pr
            LEFT JOIN users w ON w.telegram_id = pr.winner_id
            WHERE pr.status = 'resolved' AND pr.winner_id IS NOT NULL
            ORDER BY pr.resolved_at DESC, pr.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out = []
        for r in rounds:
            total_cards = conn.execute(
                "SELECT COUNT(*) FROM pvp_entries WHERE round_id = ?", (r["id"],)
            ).fetchone()[0]
            total_players = conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM pvp_entries WHERE round_id = ?", (r["id"],)
            ).fetchone()[0]
            winner_name = (
                r["winner_username"] if r["winner_username"]
                else (r["winner_first_name"] or "игрок")
            )
            out.append({
                "round_id": r["id"],
                "resolved_at": r["resolved_at"],
                "winner_name": winner_name,
                "total_cards": total_cards,
                "total_players": total_players,
            })
    return out


def resolve_due_pvp_rounds() -> list[dict]:
    """Resolves every open round whose 60s countdown has elapsed: picks one winner
    (weighted by total staked rarity-weight) and transfers every staked card, from
    every participant, to them. Called opportunistically from api.py (no background
    job) — cheap no-op when nothing's due. Returns a summary per resolved round so
    callers can notify participants via the bot."""
    now_iso = _now()
    resolved = []
    with get_conn() as conn:
        due = conn.execute(
            "SELECT id FROM pvp_rounds WHERE status = 'open' AND lock_at IS NOT NULL AND lock_at <= ?",
            (now_iso,),
        ).fetchall()
        for round_id in [r["id"] for r in due]:
            entries = conn.execute(
                """
                SELECT pe.user_id, pe.user_card_id, pe.weight, u.username, u.first_name, c.filename, c.name
                FROM pvp_entries pe
                JOIN users u ON u.telegram_id = pe.user_id
                JOIN user_cards uc ON uc.id = pe.user_card_id
                JOIN cards c ON c.id = uc.card_id
                WHERE pe.round_id = ?
                """,
                (round_id,),
            ).fetchall()
            if not entries:
                conn.execute(
                    "UPDATE pvp_rounds SET status = 'resolved', resolved_at = ? WHERE id = ?",
                    (now_iso, round_id),
                )
                continue

            weights_by_user: dict[int, int] = {}
            for e in entries:
                weights_by_user[e["user_id"]] = weights_by_user.get(e["user_id"], 0) + e["weight"]
            total = sum(weights_by_user.values())
            pick = random.uniform(0, total)
            winner_id = None
            running = 0
            for uid, w in weights_by_user.items():
                running += w
                if pick <= running:
                    winner_id = uid
                    break
            if winner_id is None:
                winner_id = list(weights_by_user.keys())[-1]

            card_ids = [e["user_card_id"] for e in entries]
            conn.executemany(
                "UPDATE user_cards SET user_id = ?, pvp_round_id = NULL WHERE id = ?",
                [(winner_id, cid) for cid in card_ids],
            )
            conn.execute(
                "UPDATE pvp_rounds SET status = 'resolved', winner_id = ?, resolved_at = ? WHERE id = ?",
                (winner_id, now_iso, round_id),
            )

            by_participant: dict[int, dict] = {}
            for e in entries:
                bucket = by_participant.setdefault(e["user_id"], {
                    "user_id": e["user_id"],
                    "username": e["username"],
                    "first_name": e["first_name"],
                    "cards": [],
                })
                bucket["cards"].append({"filename": e["filename"], "name": e["name"]})
            winner = by_participant[winner_id]
            resolved.append({
                "round_id": round_id,
                "winner_id": winner_id,
                "winner_name": winner["username"] or winner["first_name"] or "игрок",
                "total_cards": len(card_ids),
                "participants": list(by_participant.values()),
            })
    return resolved


# ---------------------------------------------------------------------------
# Admin (bot commands restricted to ADMIN_ID in bot.py)
# ---------------------------------------------------------------------------

def get_admin_stats() -> dict:
    with get_conn() as conn:
        users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        cards = conn.execute("SELECT COUNT(*) AS n FROM cards WHERE is_active = 1").fetchone()["n"]
        gems_total = conn.execute("SELECT COALESCE(SUM(gems), 0) AS n FROM users").fetchone()["n"]
        total_farmed = conn.execute("SELECT COUNT(*) AS n FROM user_cards").fetchone()["n"]
        return {"users": users, "cards": cards, "gems_total": gems_total, "total_farmed": total_farmed}


def get_card_by_id(card_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()


if __name__ == "__main__":
    init_db()
    print(f"DB initialized at {DB_PATH}")
