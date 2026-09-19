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


DAILY_BONUS_GEMS = 25
REFERRAL_REWARD_GEMS = 25
MAX_REWARDED_REFERRALS = 10  # after this many, referrals still count but stop paying out
SIGNUP_BONUS_GEMS = 50


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
        conn.execute(
            "INSERT INTO users (telegram_id, username, first_name, photo_url, ref_by, gems, gems_earned, last_daily_bonus, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (telegram_id, username, first_name, photo_url, ref_by, SIGNUP_BONUS_GEMS, SIGNUP_BONUS_GEMS, today, _now()),
        )
        if ref_by is not None and prior_referrals < MAX_REWARDED_REFERRALS:
            # signup bonus to whoever invited this brand-new player — but only for their
            # first MAX_REWARDED_REFERRALS invites; referrals beyond that still count
            # (get_referral_count keeps growing) but no longer pay out gems
            conn.execute("UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? WHERE telegram_id = ?", (REFERRAL_REWARD_GEMS, REFERRAL_REWARD_GEMS, ref_by))
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


# Tiers: silver (was rare) < gold (was epic) < platina (was legend) < diamond (new top tier).
# Tiers: bronze (junk/memes, sub-$100) < silver ($100-1k) < gold ($1k-10k) < platina ($10k-100k) < diamond (>$100k).
RARITY_WEIGHTS = {"bronze": 50, "silver": 29, "gold": 13, "platina": 7, "diamond": 1}


def draw_random_card() -> sqlite3.Row | None:
    """Pick one active card, weighted by rarity (RARITY_WEIGHTS) — most drops are BRONZE,
    then SILVER, GOLD and PLATINA progressively less common. Returns None if the catalog is empty.
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
        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (FARM_COST_GEMS, user_id))
        cur = conn.execute(
            "INSERT INTO user_cards (user_id, card_id, obtained_at) VALUES (?, ?, ?)",
            (user_id, card["id"], _now()),
        )
        user_card_id = cur.lastrowid
        drop_number = conn.execute(
            "SELECT COUNT(*) FROM user_cards WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
    return {
        "user_card_id": user_card_id,
        "card_id": card["id"],
        "filename": card["filename"],
        "name": card["name"],
        "rarity": card["rarity"],
        "drop_number": drop_number,
    }


def get_inventory(user_id: int) -> list[dict]:
    """Cards the user owns — one row per copy, shown separately even when duplicated
    (duplicates are common since supply is unlimited), each keeping its own number,
    listing/swap/stake status."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT uc.id AS user_card_id, c.id AS card_id, c.filename, c.name, c.rarity,
                   uc.listed_price, uc.swap_listed, uc.staked_at,
                   ROW_NUMBER() OVER (PARTITION BY uc.user_id ORDER BY uc.obtained_at) AS drop_number
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
    if price_gems <= 0:
        return False
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id, swap_listed, staked_at FROM user_cards WHERE id = ?", (user_card_id,)
        ).fetchone()
        if row is None or row["user_id"] != seller_id:
            return False
        if row["swap_listed"] or row["staked_at"] is not None:
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
        return {"seller_id": row["seller_id"], "price": price, "filename": row["filename"], "name": row["name"]}


def make_offer(user_card_id: int, buyer_id: int, price_gems: int) -> dict | None:
    """Buyer proposes their own price on a listed card. Seller must accept it (via bot DM)
    before anything changes hands. Returns offer + card/seller info for the notification,
    or None if the listing doesn't exist / isn't for sale / buyer == seller."""
    if price_gems <= 0:
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
        conn.execute("UPDATE market_offers SET status = 'accepted' WHERE id = ?", (offer_id,))
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
            "SELECT user_id, listed_price, swap_listed, staked_at FROM user_cards WHERE id = ?", (user_card_id,)
        ).fetchone()
        if row is None or row["user_id"] != seller_id:
            return False
        if row["listed_price"] is not None or row["swap_listed"] or row["staked_at"] is not None:
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


STAKE_REWARD_GEMS_PER_DAY = 5
STAKE_PERIOD_SECONDS = 24 * 60 * 60


def settle_staking(user_id: int) -> int:
    """Credits STAKE_REWARD_GEMS_PER_DAY gems for every full 24h period elapsed since
    each of the user's staked cards was staked (or last paid out), advancing that card's
    clock forward by exactly that many whole days so partial progress toward the next
    payout is never lost or double-paid. Called from _authenticate() on every API request
    so staking pays out passively with no background job. Returns gems credited this call."""
    now = datetime.now(timezone.utc)
    total_credited = 0
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, staked_at FROM user_cards WHERE user_id = ? AND staked_at IS NOT NULL",
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
                total_credited += full_days * STAKE_REWARD_GEMS_PER_DAY
        if total_credited:
            conn.execute(
                "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? WHERE telegram_id = ?",
                (total_credited, total_credited, user_id),
            )
    return total_credited


def stake_card(user_card_id: int, owner_id: int) -> bool:
    """Puts one owned copy into staking. Blocked if it's already staked, or currently
    listed for sale/swap (unlist first)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id, listed_price, swap_listed, staked_at FROM user_cards WHERE id = ?",
            (user_card_id,),
        ).fetchone()
        if row is None or row["user_id"] != owner_id:
            return False
        if row["listed_price"] is not None or row["swap_listed"] or row["staked_at"] is not None:
            return False
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
                SELECT uc.user_id, c.name
                FROM user_cards uc JOIN cards c ON c.id = uc.card_id
                WHERE uc.id = ?
                """,
                (oid,),
            ).fetchone()
            if row is None or row["user_id"] != buyer_id:
                return None  # buyer doesn't actually own one of the offered cards
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

        # re-verify the buyer still owns every offered card (they might have sold/given
        # one away, or it could have been claimed by another accepted offer, since we last checked)
        for oid in offered_ids:
            row = conn.execute("SELECT user_id FROM user_cards WHERE id = ?", (oid,)).fetchone()
            if row is None or row["user_id"] != offer["buyer_id"]:
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
        conn.execute("UPDATE swap_offers SET status = 'accepted' WHERE id = ?", (offer_id,))
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
    Returns {filename, name} on success, or None if the sender doesn't own the card or is
    trying to gift it to themselves."""
    if from_user_id == to_user_id:
        return None
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT uc.user_id, c.filename, c.name
            FROM user_cards uc JOIN cards c ON c.id = uc.card_id
            WHERE uc.id = ?
            """,
            (user_card_id,),
        ).fetchone()
        if row is None or row["user_id"] != from_user_id:
            return None
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
            GROUP BY u.telegram_id
            ORDER BY total_cards DESC, u.telegram_id ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]


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
