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

import os
import sqlite3
import random
import json
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

# Admin's own telegram_id — same pattern as bot.py/api.py. Used to keep the admin's
# own testing (case opens, crafts, evolutions, farmed cards) out of the /admin panel's
# player-usage stats. Not set -> nothing is excluded (behaves exactly as before).
ADMIN_ID = os.environ.get("ADMIN_ID")

DB_PATH = Path(__file__).parent / "peeppo.db"

# The user is in Tallinn, Estonia — anything framed as a calendar "day" (bot uptime
# counter, etc.) rolls over at LOCAL midnight here, not 24h after some UTC timestamp.
TALLINN_TZ = ZoneInfo("Europe/Tallinn")

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
    streak_days   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    last_seen_at  TEXT,
    gem_mining_started_at TEXT
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

CREATE TABLE IF NOT EXISTS number_giveaways (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id      INTEGER NOT NULL,
    user_card_id  INTEGER NOT NULL REFERENCES user_cards(id),
    number        INTEGER NOT NULL,
    card_name     TEXT,
    card_rarity   TEXT,
    card_filename TEXT,
    created_at    TEXT NOT NULL,
    draw_at       TEXT NOT NULL,
    drawn_at      TEXT,
    message_id    INTEGER
);

CREATE TABLE IF NOT EXISTS number_giveaway_entries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    number_giveaway_id  INTEGER NOT NULL REFERENCES number_giveaways(id),
    user_id             INTEGER NOT NULL REFERENCES users(telegram_id),
    joined_at           TEXT NOT NULL,
    UNIQUE(number_giveaway_id, user_id)
);

CREATE TABLE IF NOT EXISTS hundred_club (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    drawn_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ref_race_announced (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    announced_at  TEXT NOT NULL
);

-- Countdown pings before the referral race ends ('24h', '4h', '1h', '5m') -- separate
-- from ref_race_announced (the final results post) so each of the 4 reminders fires
-- exactly once, independent of the others and of the final announcement.
CREATE TABLE IF NOT EXISTS ref_race_countdown_sent (
    stage    TEXT PRIMARY KEY,
    sent_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_command_broadcast (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    posted_date   TEXT NOT NULL UNIQUE  -- calendar date (Europe/Tallinn), YYYY-MM-DD
);

CREATE TABLE IF NOT EXISTS aviator_rounds (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL REFERENCES users(telegram_id),
    bet               INTEGER NOT NULL,
    crash_point       REAL NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active',  -- active | won | lost
    cashout_multiplier REAL,
    created_at        TEXT NOT NULL
);

-- Minefields (Минные поля) -- one row per board. mine_positions/revealed are JSON
-- arrays of tile indices (0..MINES_GRID_TILES-1); mine_positions is only ever read
-- server-side while status='active' (see database.py's Minefields section for the
-- full game logic/RTP formula).
CREATE TABLE IF NOT EXISTS mines_rounds (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL REFERENCES users(telegram_id),
    bet                 INTEGER NOT NULL,
    mine_count          INTEGER NOT NULL,
    mine_positions      TEXT NOT NULL,
    revealed            TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT 'active',  -- active | won | lost
    cashout_multiplier  REAL,
    payout              INTEGER,
    created_at          TEXT NOT NULL,
    resolved_at         TEXT
);

-- Every Red&Black round ever played, newest first via created_at -- unlike PvP or
-- Aviator (which already had a rounds table for other reasons), play_redblack() used
-- to just mutate gems and forget the round entirely. Added purely to power the
-- in-app "Топ 10"/"История" panels for Red&Black (see get_redblack_history()/
-- get_redblack_leaderboard()); play_redblack() inserts one row per round.
CREATE TABLE IF NOT EXISTS redblack_rounds (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(telegram_id),
    bet           INTEGER NOT NULL,
    choice        TEXT NOT NULL,
    result        TEXT NOT NULL,
    won           INTEGER NOT NULL,
    payout        INTEGER NOT NULL,
    created_at    TEXT NOT NULL
);

-- American Poker '90s -- one row per hand from deal() through its eventual
-- collect()/bust. status: dealt (waiting on draw()) -> won/lost (post-draw; won means
-- payout > 0 and still open to gamble or collect) -> collected/busted (final). Gems are
-- credited/debited live at each step (draw win, each gamble win/loss) rather than held
-- in escrow, so the balance is always correct even if the client abandons a round
-- mid-gamble -- collect() just marks the round closed, no gems move there.
CREATE TABLE IF NOT EXISTS poker_rounds (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(telegram_id),
    bet            INTEGER NOT NULL,
    dealt_cards    TEXT NOT NULL,           -- JSON list of 5 card codes, e.g. ["AS","TH",...]
    deck_remaining TEXT NOT NULL,           -- JSON list of the other 47 cards, in draw order
    held_mask      TEXT,                    -- JSON list of 5 booleans, set by draw()
    final_cards    TEXT,                    -- JSON list of 5 card codes after the draw
    category       TEXT,                    -- winning hand category, or 'nothing'
    base_payout    INTEGER,                 -- payout from the draw itself, before any gambling
    current_payout INTEGER,                 -- running payout through the gamble chain; 0 once busted
    gamble_count   INTEGER NOT NULL DEFAULT 0,
    gamble_joker_used INTEGER NOT NULL DEFAULT 0,  -- the one guaranteed-win Joker reveal per round, already spent or not
    status         TEXT NOT NULL DEFAULT 'dealt',
    created_at     TEXT NOT NULL
);

-- Simple lifetime action counters for the /admin panel (cases bought, cards crafted,
-- cards evolved/burned) — bumped by open_case()/craft_card()/burn_cards() themselves,
-- see _bump_counter()/get_action_counters().
CREATE TABLE IF NOT EXISTS action_counters (
    action        TEXT PRIMARY KEY,
    count         INTEGER NOT NULL DEFAULT 0
);

-- "Продать Diamond карты за GRAM" — player requests to cash out a multiple of
-- GRAM_CARDS_PER_UNIT owned Diamond cards for GRAM crypto at a fixed rate. See
-- request_crypto_withdrawal()/admin_pay_withdrawal()/admin_cancel_withdrawal().
CREATE TABLE IF NOT EXISTS crypto_withdrawals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(telegram_id),
    card_count      INTEGER NOT NULL,
    gram_amount     INTEGER NOT NULL,
    wallet_address  TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'paid' | 'cancelled'
    created_at      TEXT NOT NULL,
    resolved_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_crypto_withdrawals_user ON crypto_withdrawals(user_id);

-- Which exact user_cards rows were held for a withdrawal request, so a cancel can
-- restore precisely those cards (same reasoning as swap_offer_cards).
CREATE TABLE IF NOT EXISTS crypto_withdrawal_cards (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    withdrawal_id  INTEGER NOT NULL REFERENCES crypto_withdrawals(id),
    user_card_id   INTEGER NOT NULL REFERENCES user_cards(id)
);

CREATE INDEX IF NOT EXISTS idx_crypto_withdrawal_cards_wd ON crypto_withdrawal_cards(withdrawal_id);

-- Card-number marketplace: drop_number is normally the live count of user_cards rows
-- with obtained_at <= this row's own (see get_inventory()) — but a row can instead
-- carry user_cards.number_override, a permanently PINNED number that always wins over
-- the computed one. That's what buying/winning a number actually sets — a real stored
-- value, not a timestamp trick — because a voided row is never deleted (FK history) and
-- still silently counts in that live formula forever, so reproducing someone's old
-- number by copying their old timestamp would double-count and drift everyone's numbers
-- who came after it. card_numbers is a small state machine keyed by the number itself:
--   free    — nobody has bid on it, open for auction at NUMBER_MIN_BID_GEMS
--   auction — has a highest_bid/highest_bidder_id, bid_expires_at counts down 24h from
--             the LAST bid; when it lapses the number flips to 'owned'
--   owned   — owner_id owns it outright; user_card_id is set when it's currently pinned
--             on one of their cards (NULL means "won but not attached yet"), and
--             list_price is set when they've put it up for resale to another player
-- See _free_number() / place_number_bid() / attach_number() / list_number_for_sale() /
-- buy_listed_number().
CREATE TABLE IF NOT EXISTS card_numbers (
    number              INTEGER PRIMARY KEY,
    status              TEXT NOT NULL DEFAULT 'free',
    owner_id            INTEGER REFERENCES users(telegram_id),
    user_card_id        INTEGER REFERENCES user_cards(id),
    highest_bid         INTEGER,
    highest_bidder_id   INTEGER REFERENCES users(telegram_id),
    bid_expires_at      TEXT,
    list_price          INTEGER,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_card_numbers_owner ON card_numbers(owner_id);

-- Custom-name marketplace ("Имена") -- same state machine as card_numbers, keyed by
-- the name string (canonical lowercase) instead of a number:
--   auction — created just now or still being bid on; highest_bid/highest_bidder_id/
--             bid_expires_at track the fight, same 12h-reset-on-every-bid rule as
--             card_numbers. Names have no 'free' status -- a name only starts
--             existing the moment someone creates it (that creation IS the first bid).
--   owned   — owner_id owns it outright; user_card_id is set when it's currently
--             attached to one of their Custom NFT cards (NULL means "in the bank,
--             not used yet"), list_price is set when listed for resale.
-- See create_name_auction() / place_name_bid() / create_custom_nft().
CREATE TABLE IF NOT EXISTS card_names (
    name                TEXT PRIMARY KEY,
    status              TEXT NOT NULL DEFAULT 'auction',
    owner_id            INTEGER REFERENCES users(telegram_id),
    user_card_id        INTEGER REFERENCES user_cards(id),
    highest_bid         INTEGER,
    highest_bidder_id   INTEGER REFERENCES users(telegram_id),
    bid_expires_at      TEXT,
    list_price          INTEGER,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_card_names_owner ON card_names(owner_id);

-- Batch card giveaways: /cardsgiveaway [часов] -- admin gives away ALL of their
-- currently-owned non-diamond cards at once, live in chat for a chosen duration.
-- Each card at stake still gets its own number_giveaways row (batch_id links them
-- together) so it stays covered for free by every existing "is this card busy in a
-- giveaway" check across the codebase (all of them query number_giveaways, not this
-- table) -- no other query anywhere needs to change. Participants join the BATCH
-- once (one button, one shared entries table below), not per-card.
CREATE TABLE IF NOT EXISTS card_batch_giveaways (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id      INTEGER NOT NULL,
    total_cards   INTEGER NOT NULL,
    created_at    TEXT NOT NULL,
    draw_at       TEXT NOT NULL,
    drawn_at      TEXT,
    message_id    INTEGER,
    chat_id       INTEGER
);

CREATE TABLE IF NOT EXISTS card_batch_giveaway_entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id      INTEGER NOT NULL REFERENCES card_batch_giveaways(id),
    user_id       INTEGER NOT NULL REFERENCES users(telegram_id),
    joined_at     TEXT NOT NULL,
    UNIQUE(batch_id, user_id)
);

-- Auto-compensation: whenever a card is retired (is_active 1 -> 0), every
-- current owner gets +25 gems per copy they hold, automatically — no matter
-- how the deactivation happens (script, admin query, anything).
CREATE TRIGGER IF NOT EXISTS trg_card_retire_compensation
AFTER UPDATE OF is_active ON cards
WHEN NEW.is_active = 0 AND OLD.is_active = 1
BEGIN
    UPDATE users SET gems = gems + 25
    WHERE telegram_id IN (SELECT user_id FROM user_cards WHERE card_id = NEW.id);
END;

-- Collections ("Альбомы") -- themed subsets of the existing card catalog the
-- player fills in one slot at a time. Placing a card in a slot never locks/consumes
-- it (it stays fully usable for market/PvP/staking) -- this is a pure completion
-- tracker, not an inventory mechanic. See _seed_collections() in database.py.
CREATE TABLE IF NOT EXISTS collections (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    key           TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    icon          TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_cards (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id  INTEGER NOT NULL REFERENCES collections(id),
    card_id        INTEGER NOT NULL REFERENCES cards(id),
    UNIQUE(collection_id, card_id)
);

CREATE TABLE IF NOT EXISTS user_collection_cards (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(telegram_id),
    collection_id  INTEGER NOT NULL REFERENCES collections(id),
    card_id        INTEGER NOT NULL REFERENCES cards(id),
    placed_at      TEXT NOT NULL,
    UNIQUE(user_id, collection_id, card_id)
);

CREATE TABLE IF NOT EXISTS user_collection_completions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(telegram_id),
    collection_id  INTEGER NOT NULL REFERENCES collections(id),
    completed_at   TEXT NOT NULL,
    UNIQUE(user_id, collection_id)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bump_counter(conn: sqlite3.Connection, action: str, by: int = 1) -> None:
    """Increments a named lifetime counter in action_counters (see get_action_counters()).
    Must be called with an already-open conn/transaction — mirrors the "one write per
    get_conn() block" pattern used everywhere else in this file."""
    conn.execute(
        "INSERT INTO action_counters (action, count) VALUES (?, ?) "
        "ON CONFLICT(action) DO UPDATE SET count = count + excluded.count",
        (action, by),
    )


def _free_number(conn: sqlite3.Connection, user_card_id: int) -> None:
    """Unpins whatever number a row CURRENTLY shows -- its number_override if it has
    one (also covers a number cycling free -> bought -> free again), otherwise the
    live count formula -- right before that row stops showing it (voided, evolved
    away, or about to be reused for a different card/number). A number can pass
    through this many times over the game's life.

    If that number is a real, previously-purchased one someone actually owns (status
    'owned' with an owner_id), it goes back into ITS OWNER's own bank (still owned,
    just unpinned) -- same rule as _detach_number_on_card_transfer() -- never released
    into the public free/auction pool just because the card that happened to be
    showing it went away. Only a genuinely natural/never-owned number (no owner on
    record) gets tracked as 'free' here."""
    row = conn.execute(
        "SELECT obtained_at, number_override FROM user_cards WHERE id = ?", (user_card_id,)
    ).fetchone()
    if row is None:
        return
    if row["number_override"] is not None:
        number = row["number_override"]
    else:
        number = conn.execute(
            "SELECT COUNT(*) FROM user_cards WHERE obtained_at <= ?", (row["obtained_at"],)
        ).fetchone()[0]
    # Guard against a coincidental collision: this card's NATURAL number (no
    # number_override) is just its rank by obtained_at, and that rank can land on
    # a number someone else already independently owns/listed/is bidding on in the
    # numbers marketplace (ranks can shift, e.g. a reused evolved-card row gets a
    # fresh obtained_at, bumping a later card down into an earlier rank). If this
    # exact number is already tracked as owned/auction AND it isn't actually the
    # one pinned on THIS card, someone else's real ownership must never be wiped.
    existing = conn.execute(
        "SELECT status, owner_id, user_card_id FROM card_numbers WHERE number = ?", (number,)
    ).fetchone()
    if existing is not None and existing["status"] in ("owned", "auction") and existing["user_card_id"] != user_card_id:
        return
    now = _now()
    if existing is not None and existing["status"] == "owned" and existing["owner_id"] is not None:
        # A real owner already holds this number (they're the one who just had it
        # unpinned) -- keep it theirs, just detach it from the card.
        conn.execute(
            "UPDATE card_numbers SET user_card_id = NULL, updated_at = ? WHERE number = ?",
            (now, number),
        )
        return
    conn.execute(
        """
        INSERT INTO card_numbers (number, status, updated_at)
        VALUES (?, 'free', ?)
        ON CONFLICT(number) DO UPDATE SET
            status = 'free', owner_id = NULL, user_card_id = NULL,
            highest_bid = NULL, highest_bidder_id = NULL, bid_expires_at = NULL,
            list_price = NULL, updated_at = excluded.updated_at
        """,
        (number, now),
    )


def _detach_number_on_card_transfer(conn: sqlite3.Connection, user_card_id: int) -> None:
    """When a card that may be carrying a pinned/purchased number changes owner
    (sold, traded, gifted, transferred, lost in PvP, given away), the NUMBER itself
    stays with whoever owns it in card_numbers (owner_id is left untouched) -- but
    card_numbers.user_card_id must stop pointing at this row, since the card no
    longer belongs to that owner. The original owner can then re-attach the freed
    number to another one of their own cards, or resell it. No-op if no card_numbers
    row currently points at this card."""
    conn.execute(
        "UPDATE card_numbers SET user_card_id = NULL, updated_at = ? WHERE user_card_id = ?",
        (_now(), user_card_id),
    )


def _release_name_on_card_void(conn: sqlite3.Connection, user_card_id: int) -> None:
    """When a card wearing a custom name stops existing or showing it (burned as
    evolve fuel, a failed Diamond craft, a crypto withdrawal), the name itself stays
    owned by whoever owned it -- just unpinned back into their own name bank ("Мои
    имена"), same rule as _detach_number_on_card_transfer()/the equivalent fix in
    _free_number() for numbers. Without this, the card_names row kept pointing at a
    card that no longer wears the name, leaving it permanently stuck showing as "in
    use" (Заюзан) with no way to free it up. No-op if no card_names row currently
    points at this card. Must be called BEFORE the card's own custom_name is cleared
    (order doesn't matter here since this only reads card_names, not user_cards)."""
    conn.execute(
        "UPDATE card_names SET user_card_id = NULL, updated_at = ? WHERE user_card_id = ?",
        (_now(), user_card_id),
    )


def get_action_counters() -> dict[str, int]:
    """Lifetime counts for the /admin panel: how many cases have been bought (opened),
    how many cards have been crafted (successful craft_card() calls that produced a new
    card), and how many cards have evolved (successful burn_cards() calls). Missing keys
    default to 0 (nothing of that kind has happened yet)."""
    with get_conn() as conn:
        rows = conn.execute("SELECT action, count FROM action_counters").fetchall()
    counts = {r["action"]: r["count"] for r in rows}
    return {
        "cases_bought": counts.get("case_open", 0),
        "cards_crafted": counts.get("craft", 0),
        "cards_evolved": counts.get("evolve", 0),
        "farms_pressed": counts.get("farm", 0),
        "cards_staked": counts.get("stake", 0),
    }


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
        if "number_override" not in uc_cols:
            conn.execute("ALTER TABLE user_cards ADD COLUMN number_override INTEGER")
        if "pinned_at" not in uc_cols:
            conn.execute("ALTER TABLE user_cards ADD COLUMN pinned_at TEXT")
        # migration for DBs created before the Custom NFT ("Obsidian") feature existed
        if "custom_name" not in uc_cols:
            conn.execute("ALTER TABLE user_cards ADD COLUMN custom_name TEXT")
        if "custom_rarity" not in uc_cols:
            conn.execute("ALTER TABLE user_cards ADD COLUMN custom_rarity TEXT")
        # migration for DBs created before gems existed
        u_cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "gems" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN gems INTEGER NOT NULL DEFAULT 0")
        # migration for DBs created before gems_earned existed (lifetime gems earned, for the Топы screen)
        if "gems_earned" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN gems_earned INTEGER NOT NULL DEFAULT 0")
        # migration for DBs created before the staking-specific earnings counter existed
        # (gems_earned mixes in every source -- this one is staking payouts only)
        if "staking_gems_earned" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN staking_gems_earned INTEGER NOT NULL DEFAULT 0")
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
        # migration for DBs created before the PvP history's win-% display existed
        pr_cols = {row["name"] for row in conn.execute("PRAGMA table_info(pvp_rounds)")}
        if "winner_win_pct" not in pr_cols:
            conn.execute("ALTER TABLE pvp_rounds ADD COLUMN winner_win_pct INTEGER")
        # migration for the in-app (not bot-DM) referral reward popup — holds the
        # referred player's display name until the referrer's client next calls
        # /api/auth, which shows it once and clears it (same read-once pattern as
        # the PvP reveal, since there's nowhere client-side to remember it either).
        if "referral_reward_notice" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN referral_reward_notice TEXT")
        # migration for last_seen_at (drives the /admin "active today" stat — updated
        # every time a player's client calls /api/auth, i.e. every time they open the app)
        if "last_seen_at" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN last_seen_at TEXT")
        # migration for gem mining (see collect_gem_mining()) — a repeatable "press to
        # start, wait 60 min, press again to collect + restart" passive gem timer.
        if "gem_mining_started_at" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN gem_mining_started_at TEXT")
        # migration for purchasable player rank (see purchase_rank()/set_purchased_rank())
        # — a floor under the normal time-based rank, bought with Telegram Stars. 0 means
        # "nothing bought", so the time-based rank alone still applies.
        if "purchased_rank_tier" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN purchased_rank_tier INTEGER NOT NULL DEFAULT 0")
        # migration for the referral-race leaderboard (/ref command + the one-time
        # scheduled announcement) — a referral only counts towards the race once the
        # referred player has both farmed at least one card AND joined PUBLIC_CHAT.
        # This flag is a cache so we don't re-check Telegram chat membership (a Bot
        # API call) for someone we've already confirmed; it never resets, so leaving
        # the chat later doesn't un-count them.
        if "chat_member_verified" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN chat_member_verified INTEGER NOT NULL DEFAULT 0")
        # migration for the login-streak feature (get_streak_info()/claim_daily_bonus())
        if "streak_days" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN streak_days INTEGER NOT NULL DEFAULT 0")
        # One-time backfill for players who signed up before the streak feature existed:
        # the column defaults to 0, but claim_daily_bonus() only ever advances it on a
        # genuine NEW-day claim, so anyone who already claimed today before this feature
        # was deployed would sit at "0 days" until their NEXT visit, which reads as a
        # bug ("I logged in today, why does it say 0?"). We have no history of exactly
        # how many consecutive days they'd already been showing up (last_daily_bonus only
        # ever stored the single most recent day, never a log), so this can't reconstruct
        # a true past streak — it just sets a fair floor of 1 for anyone who has ever
        # claimed at least once, so today counts as day 1 instead of day 0. Harmless to
        # run on every startup: claim_daily_bonus() never sets streak_days back to 0
        # itself, so once fixed a row never matches this WHERE again.
        conn.execute(
            "UPDATE users SET streak_days = 1 WHERE streak_days = 0 AND last_daily_bonus IS NOT NULL"
        )
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
        # migration for the PvP "Позвать игрока" chat ping button — cooldown so one
        # player can't spam PUBLIC_CHAT with invites (see PVP_INVITE_COOLDOWN_SECONDS).
        if "last_pvp_invite_at" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN last_pvp_invite_at TEXT")
        # migration for the /admin "unique farmers today" stat — stamped on every
        # successful farm() call, same local-date-comparison convention as
        # active_today/last_seen_at (see get_admin_stats()).
        if "last_farm_at" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN last_farm_at TEXT")
        # migration for burn_cards() — see its docstring for why this is "soft destroy"
        # (voided=1) rather than an actual DELETE.
        if "voided" not in uc_cols:
            conn.execute("ALTER TABLE user_cards ADD COLUMN voided INTEGER NOT NULL DEFAULT 0")
        # migration for list_card()'s "new" market sort fix — tracks WHEN a card was listed
        # for sale, separate from user_cards.id (which is fixed at obtain-time and never
        # reflected when an old card gets newly listed).
        if "listed_at" not in uc_cols:
            conn.execute("ALTER TABLE user_cards ADD COLUMN listed_at TEXT")
        # migration for the Aviator game (/go in bot.py) — chat_id/message_id let a
        # restart recovery pass (see refund_all_active_aviator_rounds()) edit a round's
        # frozen message when it refunds an abandoned bet, not just credit the gems back.
        av_cols = {row["name"] for row in conn.execute("PRAGMA table_info(aviator_rounds)")}
        if "chat_id" not in av_cols:
            conn.execute("ALTER TABLE aviator_rounds ADD COLUMN chat_id INTEGER")
        if "message_id" not in av_cols:
            conn.execute("ALTER TABLE aviator_rounds ADD COLUMN message_id INTEGER")
        # migration for DBs created before batch card giveaways existed
        ng_cols = {row["name"] for row in conn.execute("PRAGMA table_info(number_giveaways)")}
        if "batch_id" not in ng_cols:
            conn.execute("ALTER TABLE number_giveaways ADD COLUMN batch_id INTEGER REFERENCES card_batch_giveaways(id)")
        # migration for chat giveaway reminders ("1 hour left" / "5 minutes left" pings
        # into PUBLIC_CHAT, see bot.py's giveaway_reminder_scheduler()) -- one pair of
        # sent-flags per table so each reminder fires exactly once per giveaway.
        if "reminder_1h_sent" not in ng_cols:
            conn.execute("ALTER TABLE number_giveaways ADD COLUMN reminder_1h_sent INTEGER NOT NULL DEFAULT 0")
        if "reminder_5m_sent" not in ng_cols:
            conn.execute("ALTER TABLE number_giveaways ADD COLUMN reminder_5m_sent INTEGER NOT NULL DEFAULT 0")
        cbg_cols = {row["name"] for row in conn.execute("PRAGMA table_info(card_batch_giveaways)")}
        if "reminder_1h_sent" not in cbg_cols:
            conn.execute("ALTER TABLE card_batch_giveaways ADD COLUMN reminder_1h_sent INTEGER NOT NULL DEFAULT 0")
        if "reminder_5m_sent" not in cbg_cols:
            conn.execute("ALTER TABLE card_batch_giveaways ADD COLUMN reminder_5m_sent INTEGER NOT NULL DEFAULT 0")
        # One-time (but harmless-every-startup) backfill for poker rounds stranded in
        # status='won': the client used to only flip a round to 'collected' when the
        # player explicitly pressed "Забрать", so leaving the Poker tab (switching to
        # another game, or another screen entirely) after a win, without pressing that
        # button, left the round sitting in 'won' forever -- invisible to both
        # get_poker_history() and get_poker_leaderboard(), which both filter to
        # status IN ('collected', 'busted', 'lost'). The gems were already credited live
        # at draw/gamble time (see draw_poker()/gamble_poker()), so this is purely a
        # status flip, not a balance change -- exactly what collect_poker() itself does.
        # The client now also auto-collects on navigating away, so this should stop
        # producing new rows to fix; safe to leave running every startup regardless,
        # since a round only ever matches this WHERE once.
        conn.execute("UPDATE poker_rounds SET status = 'collected' WHERE status = 'won'")
        pr_cols = {row["name"] for row in conn.execute("PRAGMA table_info(poker_rounds)")}
        if "gamble_joker_used" not in pr_cols:
            conn.execute("ALTER TABLE poker_rounds ADD COLUMN gamble_joker_used INTEGER NOT NULL DEFAULT 0")


DAILY_BONUS_GEMS = 25
# Graduated referral payout: bigger rewards for the referrer's later invites (in the
# same signup), to encourage inviting more than just one friend. Referrals past
# MAX_REWARDED_REFERRALS still count (get_referral_count/leaderboards keep growing)
# but never pay out gems.
REFERRAL_REWARD_SCHEDULE = [25, 50, 100, 200, 300, 400, 500]  # 1st..7th referral
REFERRAL_REWARD_STEP_GEMS = 50  # +50 per referral after the 7th, up to the cap below
MAX_REWARDED_REFERRALS = 20  # after this many, referrals still count but stop paying out


def _referral_reward_for_position(position: int) -> int:
    """position is 0-indexed (0 = this referrer's 1st referral this run). Returns the
    gem reward due for that position — REFERRAL_REWARD_SCHEDULE for the first 7, then
    +REFERRAL_REWARD_STEP_GEMS per referral after that, 0 once past MAX_REWARDED_REFERRALS."""
    if position >= MAX_REWARDED_REFERRALS:
        return 0
    if position < len(REFERRAL_REWARD_SCHEDULE):
        return REFERRAL_REWARD_SCHEDULE[position]
    extra_steps = position - len(REFERRAL_REWARD_SCHEDULE) + 1
    return REFERRAL_REWARD_SCHEDULE[-1] + REFERRAL_REWARD_STEP_GEMS * extra_steps
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
            # keep profile fields fresh, never overwrite an existing ref_by; also stamp
            # last_seen_at here since this runs on every /api/auth call, i.e. every app open
            conn.execute(
                "UPDATE users SET username = ?, first_name = ?, photo_url = ?, last_seen_at = ? WHERE telegram_id = ?",
                (username, first_name, photo_url, _now(), telegram_id),
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
        ref_reward_pending = _referral_reward_for_position(prior_referrals) if ref_by is not None else 0
        total_users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        signup_bonus = EARLY_SIGNUP_BONUS_GEMS if total_users < EARLY_SIGNUP_LIMIT else SIGNUP_BONUS_GEMS
        conn.execute(
            "INSERT INTO users (telegram_id, username, first_name, photo_url, ref_by, gems, gems_earned, "
            "last_daily_bonus, streak_days, created_at, ref_reward_pending, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (telegram_id, username, first_name, photo_url, ref_by, signup_bonus, signup_bonus, today, 1, _now(), ref_reward_pending, _now()),
        )
        return conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone(), True


def get_user(telegram_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()


def _parse_utc(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _daily_bonus_amount_for(days_elapsed: int) -> int:
    """DAILY_BONUS_GEMS, +25 more for every full 30-day "month" since signup — the daily
    login reward keeps growing the longer a player sticks around."""
    return DAILY_BONUS_GEMS + 25 * (days_elapsed // 30)


def get_bot_uptime_days() -> int:
    """Days the bot has been running, counting from the very first user's created_at
    (there's no separate "bot launch date" stored anywhere, so the earliest signup is
    used as a stand-in) — day 1 is launch day itself. Counted by CALENDAR date in
    Europe/Tallinn (TALLINN_TZ), not by a raw 24h timedelta from the exact signup
    timestamp — so the "День: N" counter on the Farm screen ticks up right at local
    midnight in Tallinn, not at some arbitrary time of day tied to when the first user
    happened to sign up. Returns 1 if there are no users yet."""
    with get_conn() as conn:
        row = conn.execute("SELECT MIN(created_at) AS first FROM users").fetchone()
    if row is None or row["first"] is None:
        return 1
    first_local_date = _parse_utc(row["first"]).astimezone(TALLINN_TZ).date()
    today_local_date = datetime.now(timezone.utc).astimezone(TALLINN_TZ).date()
    days_elapsed = (today_local_date - first_local_date).days
    return days_elapsed + 1


# Player rank (NOT card rarity) — purely a function of how long someone has been
# registered, shown next to their name in Profile with the avatar ring + profile card
# border colored to match. Bronze for the first month, one tier up per month after,
# Diamond from month 5 onward.
PLAYER_RANK_COLORS = {"bronze": "#cd7f32", "silver": "#9ca3af", "gold": "#facc15", "platinum": "#a78bfa", "diamond": "#ff2fb0"}

# Ordered low -> high, index doubles as the numeric "tier" used everywhere below.
RANK_TIERS = ["bronze", "silver", "gold", "platinum", "diamond"]

# Skip the rank straight to a tier with Telegram Stars — sets a floor under the normal
# time-based rank (purchased_rank_tier on users), so it never downgrades and the
# time-based rank can still carry a player past it later for free. No bronze entry —
# it's the free starting tier, nothing to buy.
RANK_STARS_PRICE = {"silver": 200, "gold": 300, "platinum": 400, "diamond": 500}


def _time_based_rank_tier(created_at: str) -> int:
    """0-4: bronze for months 0-1 since signup, then silver/gold/platinum for months
    2/3/4, diamond from month 5 onward (30-day months, same convention as
    _daily_bonus_amount_for)."""
    months_played = (datetime.now(timezone.utc) - _parse_utc(created_at)).days // 30
    if months_played <= 1:
        return 0
    elif months_played == 2:
        return 1
    elif months_played == 3:
        return 2
    elif months_played == 4:
        return 3
    else:
        return 4


def get_player_rank_tier(user_id: int) -> int:
    """Effective 0-4 tier: the higher of the time-based rank and whatever's been bought
    (purchased_rank_tier). Falls back to 0 (bronze) if the user isn't found."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT created_at, purchased_rank_tier FROM users WHERE telegram_id = ?", (user_id,)
        ).fetchone()
    if row is None:
        return 0
    return max(_time_based_rank_tier(row["created_at"]), row["purchased_rank_tier"] or 0)


def get_player_rank(user_id: int) -> str:
    """The effective rank name (see get_player_rank_tier) — bronze/silver/gold/platinum/
    diamond. Falls back to bronze if the user isn't found."""
    return RANK_TIERS[get_player_rank_tier(user_id)]


class RankNotForSale(Exception):
    """Raised for a rank name that isn't in RANK_STARS_PRICE (bronze, or garbage)."""


def set_purchased_rank(user_id: int, rank: str) -> str:
    """Called from bot.py's successful_payment handler once Telegram confirms the Stars
    payment — sets purchased_rank_tier to the HIGHER of its current value and this rank's
    tier (never downgrades, and is a safe no-op if the player's time-based rank already
    passed this tier in the meantime — the Stars were still spent, but nothing regresses).
    Returns the resulting effective rank name. Raises RankNotForSale for an unrecognized
    rank (bronze can't be bought — it's already the free starting tier)."""
    if rank not in RANK_STARS_PRICE:
        raise RankNotForSale()
    tier = RANK_TIERS.index(rank)
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET purchased_rank_tier = MAX(purchased_rank_tier, ?) WHERE telegram_id = ?",
            (tier, user_id),
        )
    return get_player_rank(user_id)


def get_daily_bonus_info(user_id: int) -> dict:
    """Current daily-bonus amount (see _daily_bonus_amount_for) and how many days remain
    until it next steps up — for the "Доход" tile in Profile, so players can see the
    increase coming rather than just noticing it after the fact."""
    with get_conn() as conn:
        row = conn.execute("SELECT created_at FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    if row is None:
        return {"amount": DAILY_BONUS_GEMS, "days_until_next": 30}
    days_elapsed = (datetime.now(timezone.utc) - _parse_utc(row["created_at"])).days
    return {
        "amount": _daily_bonus_amount_for(days_elapsed),
        "days_until_next": 30 - (days_elapsed % 30),
    }


# Login streak — consecutive CALENDAR days (UTC, same clock as last_daily_bonus) the
# player has opened the app without a gap. Piggybacks on last_daily_bonus's existing
# "did they already claim today" gate below instead of a separate column/date: the
# streak only ever advances at the same moment the daily bonus does, so there's
# nothing to keep in sync between two independent timers.
# Streak bonus curve: checkpoints at day 1 (+5), day 10 (+25), day 20 (+50), day 30+
# (+100, the max) -- gems/day get better the longer the streak runs, with the biggest
# jump (last leg, +5/day) saved for days 20-30 as the strongest push toward the cap.
STREAK_BONUS_CHECKPOINTS = [(1, 5), (10, 25), (20, 50), (30, 100)]
STREAK_BONUS_CAP_DAYS = STREAK_BONUS_CHECKPOINTS[-1][0]
STREAK_BONUS_MAX = STREAK_BONUS_CHECKPOINTS[-1][1]


def _streak_bonus_for(streak_days: int) -> int:
    """Piecewise-linear interpolation between STREAK_BONUS_CHECKPOINTS, capped at
    STREAK_BONUS_MAX once streak_days reaches STREAK_BONUS_CAP_DAYS."""
    if streak_days <= STREAK_BONUS_CHECKPOINTS[0][0]:
        return STREAK_BONUS_CHECKPOINTS[0][1]
    if streak_days >= STREAK_BONUS_CAP_DAYS:
        return STREAK_BONUS_MAX
    for (d0, b0), (d1, b1) in zip(STREAK_BONUS_CHECKPOINTS, STREAK_BONUS_CHECKPOINTS[1:]):
        if d0 <= streak_days <= d1:
            return round(b0 + (b1 - b0) * (streak_days - d0) / (d1 - d0))
    return STREAK_BONUS_MAX  # unreachable, keeps the type checker happy


def get_streak_info(user_id: int) -> dict:
    """Current streak length and the extra gems/day it's currently worth — for the
    "День: N" tile in Profile and its info modal."""
    with get_conn() as conn:
        row = conn.execute("SELECT streak_days FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    days = row["streak_days"] if row else 0
    return {"days": days, "bonus": _streak_bonus_for(days)}


def claim_daily_bonus(user_id: int) -> int:
    """Credits the current daily-bonus amount (see _daily_bonus_amount_for — grows +25
    every 30 days since signup, plus the login-streak bonus below) once per calendar
    day (UTC) the user opens the app — returns the amount credited (0 if they already
    claimed today, or the signup day, since new users already get SIGNUP_BONUS_GEMS
    and last_daily_bonus is pre-set to that day in get_or_create_user). Also advances
    streak_days: +1 if the last credited day was yesterday, reset to 1 otherwise (a
    missed day breaks the streak, same as any login-streak feature)."""
    today_date = datetime.now(timezone.utc).date()
    today = today_date.isoformat()
    yesterday = (today_date - timedelta(days=1)).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_daily_bonus, created_at, streak_days FROM users WHERE telegram_id = ?", (user_id,)
        ).fetchone()
        if row is None or row["last_daily_bonus"] == today:
            return 0
        new_streak = (row["streak_days"] or 0) + 1 if row["last_daily_bonus"] == yesterday else 1
        days_elapsed = (datetime.now(timezone.utc) - _parse_utc(row["created_at"])).days
        amount = _daily_bonus_amount_for(days_elapsed) + _streak_bonus_for(new_streak)
        conn.execute(
            "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ?, last_daily_bonus = ?, "
            "streak_days = ? WHERE telegram_id = ?",
            (amount, amount, today, new_streak, user_id),
        )
        return amount


# ---------------------------------------------------------------------------
# Daily fortune wheel — once per calendar day (UTC), the client shows a spinning
# wheel the moment the user opens the app. The odds are intentionally never sent
# to the client (only the outcome), so the frontend's wheel segments don't have to
# match the real probabilities.
# ---------------------------------------------------------------------------

WHEEL_CHANCE_1000 = 0.01   # 1% — win 1000 gems (jackpot)
WHEEL_CHANCE_100 = 0.02    # 2% — win 100 gems
WHEEL_CHANCE_25 = 0.03     # 3% — win 25 gems
                            # (implicit 94% chance of winning nothing)


def wheel_available(user_id: int) -> bool:
    """Whether this user still has today's (UTC) fortune-wheel spin unused."""
    today = datetime.now(timezone.utc).date().isoformat()
    with get_conn() as conn:
        row = conn.execute("SELECT last_wheel_spin FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        return row is not None and row["last_wheel_spin"] != today


def spin_fortune_wheel(user_id: int) -> dict:
    """Rolls today's spin (server-side only — the odds never leave this function),
    credits any winnings, and marks the spin used for today. Returns {'ok': True,
    'amount': 0|25|100|1000} normally, or {'ok': False} if this user already spun
    today (a stale/duplicate client call)."""
    today = datetime.now(timezone.utc).date().isoformat()
    with get_conn() as conn:
        row = conn.execute("SELECT last_wheel_spin FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if row is None or row["last_wheel_spin"] == today:
            return {"ok": False, "amount": 0}
        roll = random.random()
        if roll < WHEEL_CHANCE_1000:
            amount = 1000
        elif roll < WHEEL_CHANCE_1000 + WHEEL_CHANCE_100:
            amount = 100
        elif roll < WHEEL_CHANCE_1000 + WHEEL_CHANCE_100 + WHEEL_CHANCE_25:
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


# ---------------------------------------------------------------------------
# Red/Black — a simple double-or-nothing chat game (/redblack in bot.py). One round:
# player stakes `bet` gems and picks "red" or "black"; a fair coin flip either doubles
# their stake (net +bet) or loses it outright (net -bet). No house edge, no third
# outcome (no "green zero") — deliberately simpler than real roulette.
# ---------------------------------------------------------------------------

REDBLACK_DEFAULT_BET = 25
REDBLACK_MIN_BET = 25


class RedBlackError(Exception):
    """Raised by play_redblack() for a malformed bet/choice (not a balance problem —
    that's InsufficientGems, same exception every other gem-spending action uses)."""


def play_redblack(user_id: int, bet: int, choice: str) -> dict:
    """The whole /redblack round in one atomic step: validates the bet/choice and
    balance, flips a fair 50/50 red/black result, and settles it — on a win the stake
    stays with the player plus an equal amount on top (net +bet, credited to both gems
    and gems_earned); on a loss the stake is simply removed (net -bet, gems_earned
    untouched — same convention as every other spend). Raises InsufficientGems if the
    balance can't cover the bet, RedBlackError for a bad bet amount or choice. Returns
    {"result": "red"|"black", "won": bool, "bet": int, "payout": 0 or 2*bet,
    "gems": new balance}."""
    if choice not in ("red", "black"):
        raise RedBlackError("choice must be 'red' or 'black'")
    if not isinstance(bet, int) or bet < REDBLACK_MIN_BET:
        raise RedBlackError(f"bet must be a whole number >= {REDBLACK_MIN_BET}")
    with get_conn() as conn:
        row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if row is None or row["gems"] < bet:
            raise InsufficientGems()
        result = random.choice(("red", "black"))
        won = result == choice
        if won:
            conn.execute(
                "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? WHERE telegram_id = ?",
                (bet, bet, user_id),
            )
        else:
            conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (bet, user_id))
        new_gems = conn.execute(
            "SELECT gems FROM users WHERE telegram_id = ?", (user_id,)
        ).fetchone()["gems"]
        payout = bet * 2 if won else 0
        cur = conn.execute(
            "INSERT INTO redblack_rounds (user_id, bet, choice, result, won, payout, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, bet, choice, result, int(won), payout, _now()),
        )
        round_id = cur.lastrowid
    return {"round_id": round_id, "result": result, "won": won, "bet": bet, "payout": payout, "gems": new_gems}


# ---------------------------------------------------------------------------
# Aviator — a crash-style chat game (/go in bot.py). Player stakes `bet` gems; a
# rocket's multiplier climbs through AVIATOR_TICKS (1.00x, 1.15x, 1.30x, ...) and the
# player can tap "Забрать" at any tick to cash out bet*multiplier gems, or lose the
# stake outright if the rocket crashes before they tap. The crash point is drawn ONCE,
# atomically, the instant the round starts (start_aviator()) — hidden from the player,
# stored server-side in aviator_rounds — and never recomputed later, same "decided the
# instant the bet is placed" philosophy as play_redblack()'s coin flip; the tick-by-tick
# climb in bot.py is a purely cosmetic multi-second reveal, never a delay on the RNG.
#
# crash_point = (1 - house_edge) / (1 - r) for a uniform r in [0, 1) (with an instant
# 1.00x bust when r < house_edge) is the standard crash-game formula: it makes
# E[payout] for cashing out at ANY fixed multiplier m work out to exactly bet * (1 -
# house_edge), i.e. RTP = 1 - house_edge, whichever tick the player chooses to cash out
# at — same 95% RTP flavor as a real offline casino.
#
# Race-safety needs no in-memory game state at all: every tick's "Забрать" button gets
# a FRESH callback_data baked with that tick's own multiplier, and cashout_aviator()/
# mark_aviator_crashed() both resolve via a single atomic "... WHERE status = 'active'"
# UPDATE — whichever happens first (a tap, or the loop finding the next tick
# unreachable) wins the race and the other one no-ops. A tick is only ever shown once
# peek_aviator_crash() has already confirmed the round survives past it, so any button
# still on screen is always safely payable — even a stale one left over from a bot
# restart that killed the ticking loop mid-flight.
# ---------------------------------------------------------------------------

AVIATOR_DEFAULT_BET = 25
AVIATOR_MIN_BET = 25
AVIATOR_HOUSE_EDGE = 0.03  # RTP 97%
AVIATOR_TICKS = [1.00, 1.15, 1.30, 1.50, 1.75, 2.00, 2.50, 3.00, 4.00, 5.00, 7.00, 10.00, 15.00, 20.00, 25.00, 35.00, 50.00, 75.00, 100.00]


class AviatorError(Exception):
    """Raised by start_aviator()/cashout_aviator() for a malformed bet, or a round
    that's missing/not this player's/already resolved — not a balance problem (that's
    InsufficientGems, same convention as play_redblack())."""


def start_aviator(user_id: int, bet: int) -> dict:
    """Atomically deducts the bet and draws a private crash point, then opens a new
    aviator_rounds row ('active'). Raises InsufficientGems if the balance can't cover
    the bet, AviatorError for a bad bet amount. Returns {"round_id": int, "crash_point":
    float} — crash_point is SERVER-SIDE ONLY, never send it to the client/message text."""
    if not isinstance(bet, int) or bet < AVIATOR_MIN_BET:
        raise AviatorError(f"bet must be a whole number >= {AVIATOR_MIN_BET}")
    r = random.random()
    if r < AVIATOR_HOUSE_EDGE:
        crash_point = 1.00
    else:
        crash_point = int(((1 - AVIATOR_HOUSE_EDGE) / (1 - r)) * 100) / 100
    with get_conn() as conn:
        row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if row is None or row["gems"] < bet:
            raise InsufficientGems()
        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (bet, user_id))
        cur = conn.execute(
            "INSERT INTO aviator_rounds (user_id, bet, crash_point, status, created_at) "
            "VALUES (?, ?, ?, 'active', ?)",
            (user_id, bet, crash_point, _now()),
        )
        round_id = cur.lastrowid
    return {"round_id": round_id, "crash_point": crash_point}


def peek_aviator_crash(round_id: int) -> float | None:
    """Server-side-only lookup of a round's (still hidden) crash point, used by the
    bot's tick loop to decide whether the NEXT multiplier is still reachable. Never
    exposed to the player. None if the round doesn't exist or isn't 'active' anymore."""
    with get_conn() as conn:
        row = conn.execute("SELECT crash_point, status FROM aviator_rounds WHERE id = ?", (round_id,)).fetchone()
    if row is None or row["status"] != "active":
        return None
    return row["crash_point"]


def mark_aviator_crashed(round_id: int) -> bool:
    """Called by the tick loop the instant it finds the next tick unreachable
    (crash_point <= that tick). Flips the round to 'lost' ONLY if it's still 'active'
    (atomic, race-safe against a cashout that landed a split second earlier) — the
    stake was already taken in start_aviator(), nothing more to deduct. Returns True
    if THIS call is what resolved it (caller should show the crash), False if a
    cashout already resolved this round first (caller should do nothing)."""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE aviator_rounds SET status = 'lost' WHERE id = ? AND status = 'active'",
            (round_id,),
        )
        return cur.rowcount > 0


def cashout_aviator(round_id: int, user_id: int, multiplier: float) -> dict:
    """Pays out bet*multiplier gems the instant the player taps a tick's 'Забрать'
    button — multiplier comes from that exact button (baked into its callback_data
    when the tick was drawn), never recomputed here. Atomic + race-safe: only pays
    out if the round is still 'active' and belongs to user_id; raises AviatorError if
    it's already resolved (crashed, or cashed out from another tap) or isn't this
    player's round. Returns {"bet": int, "payout": int, "multiplier": float, "gems": new balance}."""
    with get_conn() as conn:
        row = conn.execute("SELECT user_id, bet, status FROM aviator_rounds WHERE id = ?", (round_id,)).fetchone()
        if row is None:
            raise AviatorError("раунд не найден")
        if row["user_id"] != user_id:
            raise AviatorError("это не твоя игра")
        payout = int(round(row["bet"] * multiplier))
        cur = conn.execute(
            "UPDATE aviator_rounds SET status = 'won', cashout_multiplier = ? WHERE id = ? AND status = 'active'",
            (multiplier, round_id),
        )
        if cur.rowcount == 0:
            raise AviatorError("раунд уже завершён")
        conn.execute(
            "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? WHERE telegram_id = ?",
            (payout, payout, user_id),
        )
        new_gems = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()["gems"]
    return {"bet": row["bet"], "payout": payout, "multiplier": multiplier, "gems": new_gems}


AVIATOR_TICK_INTERVAL_SECONDS = 1.5  # matches bot.py's chat ticker sleep -- single source of truth


def _aviator_tick_state(crash_point: float, elapsed_seconds: float) -> tuple[str, float]:
    """Pure function: given a round's (hidden) crash_point and how long it's been
    running, returns (status, multiplier) exactly matching what bot.py's chat ticker
    would be showing right now at this exact moment -- same AVIATOR_TICK_INTERVAL_SECONDS
    cadence, same "crashes at the first tick m where crash_point <= m" rule, same
    "reaching the last tick forces an automatic win at the cap" rule. status is one of
    'flying' (still climbing, multiplier is safely cashable right now), 'crashed'
    (multiplier is where it crashed), or 'capped' (hit AVIATOR_TICKS[-1], forced
    auto-win). Used by the in-app (webapp) Aviator, which has no live chat message to
    tick -- instead of a background ticker editing a message, the client just polls
    and this is computed fresh from elapsed wall-clock time every time."""
    tick_index = min(int(elapsed_seconds // AVIATOR_TICK_INTERVAL_SECONDS), len(AVIATOR_TICKS) - 1)
    for i in range(1, tick_index + 1):
        m = AVIATOR_TICKS[i]
        if crash_point <= m:
            return "crashed", m
    if tick_index >= len(AVIATOR_TICKS) - 1:
        return "capped", AVIATOR_TICKS[-1]
    return "flying", AVIATOR_TICKS[tick_index]


def get_aviator_state(round_id: int, user_id: int) -> dict:
    """In-app polling: computes what tick the round would be showing RIGHT NOW from
    elapsed wall-clock time (see _aviator_tick_state()), purely on demand -- no
    background loop needed. If elapsed time means the round has already crashed and
    the DB still says 'active', this resolves it (mark_aviator_crashed) right here, so
    this poll (and every one after it) sees it as settled instead of staying 'active'
    forever waiting for nobody to look at it again. Raises AviatorError if the round
    doesn't exist or isn't this player's."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id, bet, crash_point, status, cashout_multiplier, created_at FROM aviator_rounds WHERE id = ?",
            (round_id,),
        ).fetchone()
    if row is None or row["user_id"] != user_id:
        raise AviatorError("раунд не найден")
    if row["status"] != "active":
        return {
            "status": row["status"], "bet": row["bet"],
            "multiplier": row["cashout_multiplier"],
            "crash_point": row["crash_point"] if row["status"] == "lost" else None,
        }
    elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(row["created_at"])).total_seconds()
    tick_status, multiplier = _aviator_tick_state(row["crash_point"], elapsed)
    if tick_status == "crashed":
        mark_aviator_crashed(round_id)
        return {"status": "lost", "bet": row["bet"], "multiplier": multiplier, "crash_point": row["crash_point"]}
    return {"status": "flying", "bet": row["bet"], "multiplier": multiplier, "capped": tick_status == "capped"}


def cashout_aviator_now(round_id: int, user_id: int) -> dict:
    """In-app cashout: the SERVER computes the current safe multiplier from elapsed
    time itself (never trusts a client-supplied value -- unlike the chat version,
    where the multiplier is safe because it's baked server-side into a Telegram
    button the player can only tap, an HTTP call could otherwise just claim any
    multiplier it likes). Raises AviatorError if the round already crashed (by
    elapsed time) or is already resolved."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id, status, crash_point, created_at FROM aviator_rounds WHERE id = ?",
            (round_id,),
        ).fetchone()
    if row is None or row["user_id"] != user_id:
        raise AviatorError("раунд не найден")
    if row["status"] != "active":
        raise AviatorError("раунд уже завершён")
    elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(row["created_at"])).total_seconds()
    tick_status, multiplier = _aviator_tick_state(row["crash_point"], elapsed)
    if tick_status == "crashed":
        mark_aviator_crashed(round_id)
        raise AviatorError("улетела -- не успел забрать")
    return cashout_aviator(round_id, user_id, multiplier)


def count_active_aviator_rounds_for_user(user_id: int) -> int:
    """In-app cap: one flying round at a time per player. chat_id IS NULL marks an
    app-created round (started via start_aviator() directly, never followed by
    set_aviator_message()) as opposed to a chat one, so this never counts/collides
    with the player's own /go games running in PUBLIC_CHAT."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM aviator_rounds WHERE status = 'active' AND user_id = ? AND chat_id IS NULL",
            (user_id,),
        ).fetchone()
    return row["n"] if row else 0


# ---------------------------------------------------------------------------
# Minefields (Минные поля) -- classic "Mines" pick-a-tile game. Player stakes `bet`
# gems and picks how many mines sit hidden among MINES_GRID_TILES tiles; the mine
# positions are drawn ONCE, atomically, the instant the round starts (start_mines()),
# same "decided the instant the bet is placed" philosophy as play_redblack()'s coin
# flip and start_aviator()'s crash point -- never recomputed later. Each safe tile the
# player reveals raises the multiplier; they can cash out any time, or keep pushing
# their luck until either a mine ends the round (stake lost) or every safe tile is
# revealed (forced auto-cashout -- nothing left to gain by continuing).
#
# multiplier(k) = MINES_RTP * prod_{i=0}^{k-1} (N - i) / (N - mines - i)  for N =
# MINES_GRID_TILES, k = tiles safely revealed so far. The un-scaled product is the
# "fair" (0% house edge) multiplier -- it's exactly the one that makes
# P(surviving k reveals) * multiplier(k) == 1, i.e. a break-even game. Scaling every
# multiplier by the same constant MINES_RTP therefore makes the expected payout from
# cashing out at ANY reveal count k work out to exactly bet * MINES_RTP -- same trick
# start_aviator() uses, just combinatorial instead of the crash-point formula.
# ---------------------------------------------------------------------------

MINES_GRID_TILES = 25
MINES_DEFAULT_BET = 25
MINES_MIN_BET = 25
MINES_RTP = 0.97  # RTP 97%
MINES_ALLOWED_MINE_COUNTS = [3, 5, 10]


class MinesError(Exception):
    """Raised by start_mines()/reveal_mines_tile()/cashout_mines() for a malformed
    bet/mine_count/tile, or a round that's missing/not this player's/already
    resolved -- not a balance problem (that's InsufficientGems)."""


def _mines_multiplier(mine_count: int, revealed_count: int) -> float:
    """Pure function -- see module-level comment above for the formula. revealed_count=0
    is always exactly 1.0 (nothing revealed yet, nothing risked)."""
    if revealed_count <= 0:
        return 1.0
    n = MINES_GRID_TILES
    fair = 1.0
    for i in range(revealed_count):
        fair *= (n - i) / (n - mine_count - i)
    return int(fair * MINES_RTP * 100) / 100


def count_active_mines_rounds_for_user(user_id: int) -> int:
    """One open board at a time per player, same convention as
    count_active_aviator_rounds_for_user()."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM mines_rounds WHERE status = 'active' AND user_id = ?",
            (user_id,),
        ).fetchone()
    return row["n"] if row else 0


def start_mines(user_id: int, bet: int, mine_count: int) -> dict:
    """Atomically deducts the bet and draws mine_count hidden mine positions among
    MINES_GRID_TILES tiles, then opens a new mines_rounds row ('active'). Raises
    InsufficientGems if the balance can't cover the bet, MinesError for a bad bet or
    mine_count. Returns {"round_id", "mine_count", "grid_tiles", "multiplier": 1.0,
    "revealed": []} -- mine positions are SERVER-SIDE ONLY, never sent to the client
    until the round ends (lost or cashed out)."""
    if not isinstance(bet, int) or bet < MINES_MIN_BET:
        raise MinesError(f"bet must be a whole number >= {MINES_MIN_BET}")
    if mine_count not in MINES_ALLOWED_MINE_COUNTS:
        raise MinesError(f"mine_count must be one of {MINES_ALLOWED_MINE_COUNTS}")
    mine_positions = random.sample(range(MINES_GRID_TILES), mine_count)
    with get_conn() as conn:
        row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if row is None or row["gems"] < bet:
            raise InsufficientGems()
        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (bet, user_id))
        cur = conn.execute(
            "INSERT INTO mines_rounds (user_id, bet, mine_count, mine_positions, revealed, status, created_at) "
            "VALUES (?, ?, ?, ?, '[]', 'active', ?)",
            (user_id, bet, mine_count, json.dumps(mine_positions), _now()),
        )
        round_id = cur.lastrowid
    return {
        "round_id": round_id, "mine_count": mine_count, "grid_tiles": MINES_GRID_TILES,
        "multiplier": 1.0, "revealed": [],
    }


def _settle_mines_won(conn: sqlite3.Connection, row: sqlite3.Row, multiplier: float) -> dict:
    """Shared settle step for both an explicit cash-out and the forced auto-cashout
    when every safe tile has been revealed. Caller already holds the open connection
    and has confirmed the round is still 'active'."""
    payout = int(round(row["bet"] * multiplier))
    conn.execute(
        "UPDATE mines_rounds SET status = 'won', cashout_multiplier = ?, payout = ?, resolved_at = ? WHERE id = ?",
        (multiplier, payout, _now(), row["id"]),
    )
    conn.execute(
        "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? WHERE telegram_id = ?",
        (payout, payout, row["user_id"]),
    )
    new_gems = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (row["user_id"],)).fetchone()["gems"]
    return {
        "status": "won", "payout": payout, "multiplier": multiplier, "gems": new_gems,
        "mine_positions": json.loads(row["mine_positions"]), "round_id": row["id"],
    }


def reveal_mines_tile(round_id: int, user_id: int, tile: int) -> dict:
    """Opens one tile. A mine ends the round right there (stake already spent at
    start_mines() -- nothing more to deduct); a safe tile raises the multiplier and,
    if that was the LAST safe tile on the board, auto-cashes-out immediately (nothing
    left to gain by continuing, and no more tiles to offer). Raises MinesError for an
    out-of-range/already-revealed tile, or a round that's missing/not this player's/
    already resolved."""
    if not isinstance(tile, int) or not (0 <= tile < MINES_GRID_TILES):
        raise MinesError("tile out of range")
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM mines_rounds WHERE id = ?", (round_id,)).fetchone()
        if row is None or row["user_id"] != user_id:
            raise MinesError("раунд не найден")
        if row["status"] != "active":
            raise MinesError("раунд уже завершён")
        mine_positions = json.loads(row["mine_positions"])
        revealed = json.loads(row["revealed"])
        if tile in revealed:
            raise MinesError("эта клетка уже открыта")
        if tile in mine_positions:
            conn.execute(
                "UPDATE mines_rounds SET status = 'lost', payout = 0, resolved_at = ? WHERE id = ?",
                (_now(), round_id),
            )
            return {"status": "lost", "mine_positions": mine_positions, "tile": tile, "round_id": round_id}
        revealed.append(tile)
        multiplier = _mines_multiplier(row["mine_count"], len(revealed))
        if len(revealed) >= MINES_GRID_TILES - row["mine_count"]:
            # Every safe tile is now open -- nothing left to reveal, force the cashout.
            result = _settle_mines_won(conn, row, multiplier)
            result["revealed"] = revealed
            return result
        conn.execute(
            "UPDATE mines_rounds SET revealed = ? WHERE id = ?",
            (json.dumps(revealed), round_id),
        )
    return {"status": "active", "multiplier": multiplier, "revealed": revealed}


def cashout_mines(round_id: int, user_id: int) -> dict:
    """Cashes out at whatever multiplier the player's current reveals are worth.
    Raises MinesError if nothing has been revealed yet (nothing to cash out at 1.00x),
    or the round is missing/not this player's/already resolved."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM mines_rounds WHERE id = ?", (round_id,)).fetchone()
        if row is None or row["user_id"] != user_id:
            raise MinesError("раунд не найден")
        if row["status"] != "active":
            raise MinesError("раунд уже завершён")
        revealed = json.loads(row["revealed"])
        if not revealed:
            raise MinesError("сначала открой хотя бы одну клетку")
        multiplier = _mines_multiplier(row["mine_count"], len(revealed))
        result = _settle_mines_won(conn, row, multiplier)
        result["revealed"] = revealed
    return result


def get_mines_state(round_id: int, user_id: int) -> dict:
    """For a client reload mid-round -- returns exactly what the board should be
    showing right now. Resolved rounds include mine_positions (board reveal)."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM mines_rounds WHERE id = ?", (round_id,)).fetchone()
    if row is None or row["user_id"] != user_id:
        raise MinesError("раунд не найден")
    revealed = json.loads(row["revealed"])
    if row["status"] != "active":
        return {
            "status": row["status"], "bet": row["bet"], "mine_count": row["mine_count"],
            "revealed": revealed, "multiplier": row["cashout_multiplier"],
            "mine_positions": json.loads(row["mine_positions"]),
        }
    return {
        "status": "active", "bet": row["bet"], "mine_count": row["mine_count"],
        "revealed": revealed, "multiplier": _mines_multiplier(row["mine_count"], len(revealed)),
    }


def get_active_mines_round_for_user(user_id: int) -> dict | None:
    """For app reload/reconnect recovery: returns this player's currently-open
    ('active') Mines board, in the same shape as get_mines_state()'s active branch,
    or None if they have no active round. The frontend calls this once when
    switching into the Mines tab so a round orphaned by a crash/reload/backgrounded
    app is never permanently stuck -- previously ONLY in-memory JS state tracked the
    active round_id, so losing that state client-side (app fully closed/reopened,
    JS error, etc.) left the server thinking a board was still open forever, which
    silently blocked every future /mines/start with "already have an active board"
    and zero way for the player to recover short of an admin fixing the DB by hand."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM mines_rounds WHERE user_id = ? AND status = 'active' "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    revealed = json.loads(row["revealed"])
    return {
        "round_id": row["id"], "bet": row["bet"], "mine_count": row["mine_count"],
        "revealed": revealed, "multiplier": _mines_multiplier(row["mine_count"], len(revealed)),
    }


def get_mines_round(round_id: int) -> dict | None:
    """One Mines round by id, joined with the player's username/first_name -- same
    convention as get_redblack_round()/get_aviator_round() (used by the "Поделиться"
    share endpoint, and could back a per-round lookup elsewhere later)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT mr.*, u.username, u.first_name FROM mines_rounds mr "
            "JOIN users u ON u.telegram_id = mr.user_id WHERE mr.id = ?",
            (round_id,),
        ).fetchone()
        return dict(row) if row else None


def get_mines_history(limit: int = 50) -> list[dict]:
    """Every resolved Mines round (won or lost -- 'active' ones are still in
    progress and excluded), newest first, for the "История" panel. Same convention
    as get_redblack_history()/get_aviator_history()."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT mr.id, mr.user_id, mr.bet, mr.mine_count, mr.status, mr.cashout_multiplier, "
            "mr.payout, mr.created_at, u.username, u.first_name "
            "FROM mines_rounds mr JOIN users u ON u.telegram_id = mr.user_id "
            "WHERE mr.status IN ('won', 'lost') "
            "ORDER BY mr.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def _house_stats_row(rounds: int, wagered: int, paid: int) -> dict:
    return {
        "rounds": rounds, "wagered": wagered, "paid": paid, "profit": wagered - paid,
        "effective_rtp": round(paid / wagered * 100, 2) if wagered else None,
    }


def get_redblack_house_stats() -> dict:
    """Same shape as get_mines_house_stats() -- see there for what each field means.
    Excludes LEADERBOARD_EXCLUDED_USERNAMES (the dev's own test play) so RTP reflects
    real players, not testing noise."""
    with get_conn() as conn:
        excl_placeholders = ",".join("?" for _ in LEADERBOARD_EXCLUDED_USERNAMES)
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(rr.bet), 0) AS wagered, COALESCE(SUM(rr.payout), 0) AS paid "
            "FROM redblack_rounds rr JOIN users u ON u.telegram_id = rr.user_id "
            f"WHERE LOWER(COALESCE(u.username, '')) NOT IN ({excl_placeholders})",
            list(LEADERBOARD_EXCLUDED_USERNAMES),
        ).fetchone()
    return _house_stats_row(row["n"], row["wagered"], row["paid"])


def get_aviator_house_stats() -> dict:
    """Same shape as get_mines_house_stats(). aviator_rounds has no stored payout
    column (only cashout_multiplier), so paid is recomputed here the same way
    cashout_aviator() itself rounds it: ROUND(bet * cashout_multiplier). Excludes
    LEADERBOARD_EXCLUDED_USERNAMES (the dev's own test play) so RTP reflects real
    players, not testing noise."""
    with get_conn() as conn:
        excl_placeholders = ",".join("?" for _ in LEADERBOARD_EXCLUDED_USERNAMES)
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(ar.bet), 0) AS wagered, "
            "COALESCE(SUM(CASE WHEN ar.status = 'won' THEN CAST(ROUND(ar.bet * ar.cashout_multiplier) AS INTEGER) ELSE 0 END), 0) AS paid "
            "FROM aviator_rounds ar JOIN users u ON u.telegram_id = ar.user_id "
            f"WHERE ar.status IN ('won', 'lost') AND LOWER(COALESCE(u.username, '')) NOT IN ({excl_placeholders})",
            list(LEADERBOARD_EXCLUDED_USERNAMES),
        ).fetchone()
    return _house_stats_row(row["n"], row["wagered"], row["paid"])


def get_poker_house_stats() -> dict:
    """Same shape as get_mines_house_stats(). current_payout is already 0 for a
    'busted' round and the settled amount for 'collected'/'lost', same field the
    in-app history/leaderboard use. Excludes LEADERBOARD_EXCLUDED_USERNAMES (the
    dev's own test play) so RTP reflects real players, not testing noise."""
    with get_conn() as conn:
        excl_placeholders = ",".join("?" for _ in LEADERBOARD_EXCLUDED_USERNAMES)
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(pr.bet), 0) AS wagered, COALESCE(SUM(pr.current_payout), 0) AS paid "
            "FROM poker_rounds pr JOIN users u ON u.telegram_id = pr.user_id "
            f"WHERE pr.status IN ('collected', 'busted', 'lost') AND LOWER(COALESCE(u.username, '')) NOT IN ({excl_placeholders})",
            list(LEADERBOARD_EXCLUDED_USERNAMES),
        ).fetchone()
    return _house_stats_row(row["n"], row["wagered"], row["paid"])


def get_all_house_stats() -> dict:
    """One-stop combined view for /housestats -- every gems-wagering game's real
    running wagered/paid/profit, side by side."""
    return {
        "mines": get_mines_house_stats(),
        "redblack": get_redblack_house_stats(),
        "aviator": get_aviator_house_stats(),
        "poker": get_poker_house_stats(),
    }


def get_mines_house_stats() -> dict:
    """Total gems wagered vs. paid out across every resolved Mines round -- the
    house's actual running profit/loss for this game (not just the theoretical 97%
    RTP the multiplier formula is built on -- this is what really happened). profit
    is wagered - paid (positive = house ahead, negative = house is down real gems).
    Excludes LEADERBOARD_EXCLUDED_USERNAMES (the dev's own test play) so RTP reflects
    real players, not testing noise. Backs /minesstats and /housestats."""
    with get_conn() as conn:
        excl_placeholders = ",".join("?" for _ in LEADERBOARD_EXCLUDED_USERNAMES)
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(mr.bet), 0) AS wagered, COALESCE(SUM(mr.payout), 0) AS paid "
            "FROM mines_rounds mr JOIN users u ON u.telegram_id = mr.user_id "
            f"WHERE mr.status IN ('won', 'lost') AND LOWER(COALESCE(u.username, '')) NOT IN ({excl_placeholders})",
            list(LEADERBOARD_EXCLUDED_USERNAMES),
        ).fetchone()
    return _house_stats_row(row["n"], row["wagered"], row["paid"])


def get_mines_leaderboard(limit: int = 10) -> list[dict]:
    """Top players by total net gems won across every resolved Mines round (won:
    payout - bet, lost: -bet), highest first. Same LEADERBOARD_EXCLUDED_USERNAMES
    convention as every other leaderboard."""
    with get_conn() as conn:
        excl_placeholders = ",".join("?" for _ in LEADERBOARD_EXCLUDED_USERNAMES)
        rows = conn.execute(
            f"""
            SELECT mr.user_id AS telegram_id, u.username AS username, u.first_name AS first_name,
                   SUM(CASE WHEN mr.status = 'won' THEN mr.payout - mr.bet ELSE -mr.bet END) AS net_profit
            FROM mines_rounds mr JOIN users u ON u.telegram_id = mr.user_id
            WHERE mr.status IN ('won', 'lost')
            AND LOWER(COALESCE(u.username, '')) NOT IN ({excl_placeholders})
            GROUP BY mr.user_id
            ORDER BY net_profit DESC LIMIT ?
            """,
            (*LEADERBOARD_EXCLUDED_USERNAMES, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# American Poker '90s -- classic 5-card draw video poker with a double-up (gamble)
# feature, dealt/played entirely server-side (client only ever sends a bet, a
# hold_mask, or a red/black gamble choice -- it never sees hidden cards it shouldn't).
# Paytable's SHAPE (which hand pays how much relative to the others) matches a real
# "American Poker" cabinet's payout card (per user reference photo): no payout below
# two pair, five_of_a_kind (Joker-only) added above royal flush. The exact multipliers
# had to be scaled down from the reference photo's raw numbers, though -- those are
# calibrated for a normal 52-card deck, and our deck carries one Joker wild card (see
# _poker_evaluate()), which makes every category hit noticeably more often than on a
# real machine. Using the reference photo's numbers as-is simulated at ~189% RTP with
# the client's actual hold-suggestion strategy (see poker_rtp_sim.py-style check) --
# the house would lose gems on every hand on average. These multipliers were solved
# against that same simulation to land close to a 97% target (comes out ~97.9%,
# same ballpark as real 8/5 Jacks-or-Better's 97.298% -- an exact 97.00% isn't a
# realistic target for any discrete integer paytable). Re-run that simulation whenever
# this table changes -- looking "reasonable" next to a real machine's numbers doesn't
# mean the RTP is anywhere close once the joker's extra win-rate is in the mix.
# ---------------------------------------------------------------------------
POKER_MIN_BET = 1
POKER_PAYTABLE = {
    "five_of_a_kind": 300,  # only possible with the Joker: 4 naturally-matching cards + it -- the rarest hand, so it pays MORE than a royal flush
    "royal_flush": 150,
    "straight_flush": 40,
    "four_kind": 20,
    "full_house": 5,
    "flush": 5,
    "straight": 4,
    "three_kind": 3,
    "two_pair": 1,
    # no "jacks_or_better" entry -- a lone pair no longer pays (see comment above).
    # Rescaled back DOWN close to this original table after pokerSuggestHold() was
    # tightened again per request: it now holds ONLY a made pair/two pair/trips, a real
    # 4-card flush draw, or a real 4-card straight draw -- no more holding a lone high
    # card, 2 suited high cards, or 3 cards to a royal, since none of those pay and a
    # fresh 5-card redraw beats holding dead cards. That strategy is close to the
    # ORIGINAL (pre-inflation) one this table was designed for, so RTP landed back near
    # ~96.3% at these near-original numbers (full_house nudged 6->5 to fine-tune) instead
    # of the ~123% the previous (inflated) numbers gave under this stricter strategy.
    # Re-run the RTP simulation whenever this table OR pokerSuggestHold() changes -- the
    # two are coupled, changing one without the other silently moves RTP.
}
# Double-up ladder: 6 plain doublings off the base win, then a 7th "bonus" step --
# NOT a fixed multiplier off the base (that produced arbitrary-looking totals like
# 66500/45000 depending on the bet+hand that got you there). Per request, the bonus
# rung instead roughly doubles whatever level 6 actually paid and rounds UP to the
# nearest POKER_BONUS_ROUND_TO so it always lands on a clean, good-looking number --
# see gamble_poker() for the exact formula. Confirmed against examples given: level6
# 2400 -> 5000, 7200 -> 15000, 18500 -> 40000 (all = ceil(level6*2/5000)*5000).
POKER_DOUBLE_MULTIPLIERS = [2, 4, 8, 16, 32, 64]  # levels 1-6 only -- fixed cumulative multiplier off the base
POKER_BONUS_ROUND_TO = 5000
POKER_MAX_GAMBLES = len(POKER_DOUBLE_MULTIPLIERS) + 1  # 7 -- the 7th ("bonus") rung is computed dynamically, once here must collect
POKER_DOUBLE_JOKER_CHANCE = 0.0  # disabled 2026-09-29: an independent guaranteed-win
# chance stacked ON TOP of the fair 50/50 flip gave every un-used gamble step a real
# 56% win chance for a flat x2 payout -- confirmed via simulation this made "always
# gamble to the max" worth ~1.89x the base payout in expectation (should be ~1.0x for
# a fair double-or-nothing). This is what was pushing Poker's real RTP to ~149%. Kept
# at 0.0 rather than deleted so the joker UI/result plumbing can be reused later if a
# properly-compensated version is designed.

POKER_RANKS = "23456789TJQKA"
POKER_SUITS = "SHDC"
POKER_JOKER = "JK"  # unambiguous: "K" is never a rank+suit pair since K isn't in POKER_SUITS


def _poker_fresh_deck() -> list[str]:
    return [r + s for r in POKER_RANKS for s in POKER_SUITS] + [POKER_JOKER]


def _poker_rank_value(card: str) -> int:
    return POKER_RANKS.index(card[0]) + 2


# Strength order for _poker_evaluate()'s joker substitution search below -- higher wins.
# five_of_a_kind isn't listed: it's handled as its own special case (see _poker_evaluate),
# since no substitute card from the deck can ever produce it (the 4 matching naturals
# already use up every card of that rank -- there's no 5th one left to substitute in).
_POKER_CATEGORY_STRENGTH = {
    "royal_flush": 9, "straight_flush": 8, "four_kind": 7, "full_house": 6,
    "flush": 5, "straight": 4, "three_kind": 3, "two_pair": 2,
    "jacks_or_better": 1, "nothing": 0,
}


def _poker_evaluate_natural(cards: list[str]) -> str:
    """Standard 5-card poker hand ranking (no joker involved), collapsed to just the
    categories POKER_PAYTABLE actually pays -- a made pair below Jacks is 'nothing'
    (doesn't pay), same as a real Jacks-or-Better machine."""
    ranks = sorted((_poker_rank_value(c) for c in cards), reverse=True)
    suits = [c[1] for c in cards]
    is_flush = len(set(suits)) == 1
    rc: dict[int, int] = {}
    for r in ranks:
        rc[r] = rc.get(r, 0) + 1
    counts = sorted(rc.values(), reverse=True)
    uniq = sorted(set(ranks), reverse=True)
    is_straight = False
    if len(uniq) == 5:
        if uniq[0] - uniq[4] == 4:
            is_straight = True
        elif uniq == [14, 5, 4, 3, 2]:  # wheel: A-2-3-4-5
            is_straight = True
    if is_straight and is_flush:
        if sorted(uniq) == [10, 11, 12, 13, 14]:
            return "royal_flush"
        return "straight_flush"
    if counts[0] == 4:
        return "four_kind"
    if counts[0] == 3 and counts[1] == 2:
        return "full_house"
    if is_flush:
        return "flush"
    if is_straight:
        return "straight"
    if counts[0] == 3:
        return "three_kind"
    if counts[0] == 2 and counts[1] == 2:
        return "two_pair"
    if counts[0] == 2:
        pair_rank = [r for r, c in rc.items() if c == 2][0]
        if pair_rank >= 11:  # Jack (11) or better
            return "jacks_or_better"
        return "nothing"
    return "nothing"


def _poker_evaluate(cards: list[str]) -> str:
    """Wraps _poker_evaluate_natural() with joker-wild handling. The deck carries
    exactly one POKER_JOKER, so a hand has 0 or 1 of it -- never more:
    - No joker: plain _poker_evaluate_natural().
    - Joker + 4 naturals all the same rank: there's no 5th real card of that rank left
      in the deck to try as a substitute, so this can ONLY be five_of_a_kind -- handled
      directly rather than via the search below.
    - Joker + anything else: brute-force every one of the (up to 52) real cards not
      already in hand as the joker's stand-in, evaluate the resulting natural 5-card
      hand each time, and keep whichever substitution scores highest per
      _POKER_CATEGORY_STRENGTH -- i.e. the joker always completes the best hand it can
      (a royal flush if one's reachable, a made pair if nothing better is, etc.)."""
    if POKER_JOKER not in cards:
        return _poker_evaluate_natural(cards)
    others = [c for c in cards if c != POKER_JOKER]
    if len({c[0] for c in others}) == 1:
        return "five_of_a_kind"
    used = set(others)
    best_category = "nothing"
    best_strength = -1
    for r in POKER_RANKS:
        for s in POKER_SUITS:
            candidate = r + s
            if candidate in used:
                continue
            category = _poker_evaluate_natural(others + [candidate])
            strength = _POKER_CATEGORY_STRENGTH.get(category, 0)
            if strength > best_strength:
                best_strength = strength
                best_category = category
    return best_category


class PokerError(Exception):
    """Raised by the poker functions for a bad bet/state -- not a balance problem
    (that's InsufficientGems, same convention as every other gem-spending action)."""


def deal_poker(user_id: int, bet: int) -> dict:
    """Charges `bet` gems, deals a fresh 5-card hand, and stashes the rest of the
    shuffled deck server-side for the draw step. Raises PokerError for a bad bet,
    InsufficientGems if the balance can't cover it."""
    if not isinstance(bet, int) or bet < POKER_MIN_BET:
        raise PokerError(f"bet must be a whole number >= {POKER_MIN_BET}")
    deck = _poker_fresh_deck()
    random.shuffle(deck)
    hand, rest = deck[:5], deck[5:]
    with get_conn() as conn:
        row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if row is None or row["gems"] < bet:
            raise InsufficientGems()
        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (bet, user_id))
        cur = conn.execute(
            "INSERT INTO poker_rounds (user_id, bet, dealt_cards, deck_remaining, status, created_at) "
            "VALUES (?, ?, ?, ?, 'dealt', ?)",
            (user_id, bet, json.dumps(hand), json.dumps(rest), _now()),
        )
        round_id = cur.lastrowid
        new_gems = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()["gems"]
    return {"round_id": round_id, "cards": hand, "bet": bet, "gems": new_gems}


def draw_poker(round_id: int, user_id: int, hold_mask: list) -> dict:
    """Replaces every non-held card from the server-stashed deck, evaluates the final
    hand, and -- if it pays -- credits the win immediately (still open to gamble via
    gamble_poker() or close out via collect_poker()). Raises PokerError if the round
    doesn't exist, isn't this player's, isn't in 'dealt' status, or hold_mask is the
    wrong shape."""
    if not isinstance(hold_mask, list) or len(hold_mask) != 5:
        raise PokerError("hold_mask must be a list of 5 booleans")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM poker_rounds WHERE id = ? AND user_id = ?", (round_id, user_id)
        ).fetchone()
        if row is None or row["status"] != "dealt":
            raise PokerError("round not found or already drawn")
        hand = json.loads(row["dealt_cards"])
        rest = json.loads(row["deck_remaining"])
        ri = 0
        final = []
        for held, card in zip(hold_mask, hand):
            if held:
                final.append(card)
            else:
                final.append(rest[ri])
                ri += 1
        category = _poker_evaluate(final)
        multiplier = POKER_PAYTABLE.get(category, 0)
        payout = row["bet"] * multiplier
        status = "won" if payout > 0 else "lost"
        if payout > 0:
            conn.execute(
                "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? WHERE telegram_id = ?",
                (payout, payout, user_id),
            )
        conn.execute(
            "UPDATE poker_rounds SET final_cards = ?, held_mask = ?, category = ?, base_payout = ?, "
            "current_payout = ?, status = ? WHERE id = ?",
            (json.dumps(final), json.dumps(hold_mask), category, payout, payout, status, round_id),
        )
        new_gems = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()["gems"]
    return {
        "round_id": round_id, "cards": final, "category": category,
        "multiplier": multiplier, "payout": payout, "gems": new_gems,
        "can_gamble": payout > 0,
    }


def gamble_poker(round_id: int, user_id: int, choice: str) -> dict:
    """Classic double-up ladder: a fair 50/50 red/black flip moves you up ONE rung of
    POKER_DOUBLE_MULTIPLIERS (applied to the round's original base_payout, not simply
    doubling the running total) -- win and gems are topped up by exactly the difference
    from the current payout (so the running DB balance is always correct regardless of
    chain length -- see the module-level comment on POKER_DOUBLE_MULTIPLIERS for why the
    last rung isn't a plain double), lose and the whole running payout (already-credited
    gems) is taken back. Raises PokerError if the round isn't open to gamble or the
    ladder top has been reached."""
    if choice not in ("red", "black"):
        raise PokerError("choice must be 'red' or 'black'")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM poker_rounds WHERE id = ? AND user_id = ?", (round_id, user_id)
        ).fetchone()
        if row is None or row["status"] != "won":
            raise PokerError("round not found or not open for gamble")
        if row["gamble_count"] >= POKER_MAX_GAMBLES:
            raise PokerError("max gambles reached -- collect your winnings")
        joker_used = row["gamble_joker_used"]
        joker_hit = joker_used == 0 and random.random() < POKER_DOUBLE_JOKER_CHANCE
        if joker_hit:
            result = "joker"
            won = True  # the Joker always wins, whichever color the player picked
        else:
            result = random.choice(("red", "black"))
            won = result == choice
        current = row["current_payout"]
        new_level = row["gamble_count"] + 1
        new_joker_used = 1 if joker_hit else joker_used
        if won:
            if new_level == POKER_MAX_GAMBLES:
                # Bonus (7th) rung: not a fixed multiplier off the base -- roughly
                # double whatever level 6 actually paid, rounded UP to a clean number.
                level6_payout = row["base_payout"] * POKER_DOUBLE_MULTIPLIERS[-1]
                doubled = level6_payout * 2
                new_payout = -(-doubled // POKER_BONUS_ROUND_TO) * POKER_BONUS_ROUND_TO  # ceil division
            else:
                new_payout = row["base_payout"] * POKER_DOUBLE_MULTIPLIERS[new_level - 1]
            increment = new_payout - current
            conn.execute(
                "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? WHERE telegram_id = ?",
                (increment, increment, user_id),
            )
            conn.execute(
                "UPDATE poker_rounds SET current_payout = ?, gamble_count = ?, gamble_joker_used = ? WHERE id = ?",
                (new_payout, new_level, new_joker_used, round_id),
            )
            new_status = "won"
        else:
            new_payout = 0
            conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (current, user_id))
            conn.execute(
                "UPDATE poker_rounds SET current_payout = 0, status = 'busted', gamble_count = ?, "
                "gamble_joker_used = ? WHERE id = ?",
                (new_level, new_joker_used, round_id),
            )
            new_status = "busted"
        new_gems = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()["gems"]
    return {
        "result": result, "won": won, "payout": new_payout, "gems": new_gems,
        "status": new_status, "gamble_count": new_level,
        "can_gamble": new_status == "won" and new_level < POKER_MAX_GAMBLES,
    }


def collect_poker(round_id: int, user_id: int) -> dict:
    """Closes out a 'won' round -- gems were already credited live at draw/gamble time,
    so this is just a status flip, not a balance change."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM poker_rounds WHERE id = ? AND user_id = ?", (round_id, user_id)
        ).fetchone()
        if row is None or row["status"] != "won":
            raise PokerError("round not found or not open to collect")
        conn.execute("UPDATE poker_rounds SET status = 'collected' WHERE id = ?", (round_id,))
        gems = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()["gems"]
    return {"payout": row["current_payout"], "gems": gems}


def get_poker_round(round_id: int) -> dict | None:
    """One Poker round by id, joined with the player's username/first_name -- same
    pattern as get_redblack_round(), used by the "Поделиться" share endpoint to verify
    ownership + build the message server-side."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT pr.*, u.username, u.first_name FROM poker_rounds pr "
            "JOIN users u ON u.telegram_id = pr.user_id WHERE pr.id = ?",
            (round_id,),
        ).fetchone()
        return dict(row) if row else None


def get_poker_history(limit: int = 50) -> list[dict]:
    """Every FINISHED Poker round (collected, busted, or lost -- never an in-progress
    'dealt'/'won' one still waiting on a draw/gamble/collect), newest first -- same
    convention as get_redblack_history()."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT pr.id, pr.user_id, pr.bet, pr.category, pr.current_payout, pr.status, "
            "pr.gamble_count, pr.created_at, u.username, u.first_name "
            "FROM poker_rounds pr JOIN users u ON u.telegram_id = pr.user_id "
            "WHERE pr.status IN ('collected', 'busted', 'lost') "
            "ORDER BY pr.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_poker_leaderboard(limit: int = 10) -> list[dict]:
    """Top players by total net gems won across every finished Poker round
    (current_payout - bet per round, summed -- this telescopes correctly through any
    gamble chain since gems are credited/debited live at each step, see draw_poker()/
    gamble_poker()). Same LEADERBOARD_EXCLUDED_USERNAMES convention as every other
    leaderboard."""
    with get_conn() as conn:
        excl_placeholders = ",".join("?" for _ in LEADERBOARD_EXCLUDED_USERNAMES)
        rows = conn.execute(
            "SELECT pr.user_id AS telegram_id, u.username AS username, u.first_name AS first_name, "
            "SUM(pr.current_payout - pr.bet) AS net_profit, COUNT(*) AS rounds_played "
            "FROM poker_rounds pr JOIN users u ON u.telegram_id = pr.user_id "
            "WHERE pr.status IN ('collected', 'busted', 'lost') "
            f"AND LOWER(COALESCE(u.username, '')) NOT IN ({excl_placeholders}) "
            "GROUP BY pr.user_id ORDER BY net_profit DESC LIMIT ?",
            list(LEADERBOARD_EXCLUDED_USERNAMES) + [limit],
        ).fetchall()
        return [dict(r) for r in rows]


def set_aviator_message(round_id: int, chat_id: int, message_id: int) -> None:
    """Attaches the (chat_id, message_id) of the round's live message once it's been
    sent — round_id has to exist BEFORE the message can be sent (its id goes into the
    first cashout button's callback_data), so this is a small follow-up write, not
    part of start_aviator() itself. Lets a later restart-recovery pass (see
    refund_all_active_aviator_rounds()) edit the frozen message when it force-refunds
    an abandoned round, instead of only silently crediting the gems back."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE aviator_rounds SET chat_id = ?, message_id = ? WHERE id = ?",
            (chat_id, message_id, round_id),
        )


def has_daily_command_broadcast_posted(date_str: str) -> bool:
    """date_str is a calendar date (YYYY-MM-DD) in whatever timezone the caller's
    schedule uses (bot.py's daily_help_broadcast_scheduler uses Europe/Tallinn, same
    convention as gem drops/daily bonus rollover). Used to make the once-a-day chat
    command reminder idempotent against the scheduler's own polling granularity and
    against a bot restart landing inside the same hour."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM daily_command_broadcast WHERE posted_date = ?", (date_str,)
        ).fetchone()
    return row is not None


def mark_daily_command_broadcast_posted(date_str: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO daily_command_broadcast (posted_date) VALUES (?) ON CONFLICT(posted_date) DO NOTHING",
            (date_str,),
        )


def count_active_aviator_rounds_in_chat(chat_id: int) -> int:
    """How many aviator_rounds rows are currently 'active' in this specific chat --
    used to cap concurrent /go games per chat. Many tickers all editing their own
    message roughly once a second, in the SAME chat, is what trips Telegram's
    per-chat flood control and freezes the animation for everyone in it at once
    (confirmed in prod logs: a burst of several updates all stuck 20-27s, finishing
    within the same second -- classic 'all waiting on the same shared rate limit,
    released together' shape), not just the newest game."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM aviator_rounds WHERE status = 'active' AND chat_id = ?",
            (chat_id,),
        ).fetchone()
    return row["n"] if row else 0


def get_stale_active_aviator_rounds(older_than_seconds: int = 0) -> list[dict]:
    """Every aviator_rounds row still 'active' and older than older_than_seconds —
    i.e. a round whose bet was taken but never resolved (won/lost/refunded). Used
    both at bot startup (older_than_seconds=0 -- ANY leftover 'active' row is from a
    prior process that died mid-round, since a live process always resolves its own
    rounds) and by a periodic watchdog (a generous threshold, catching a round whose
    in-process ticker died some other way without a restart)."""
    cutoff = _now_minus_seconds(older_than_seconds) if older_than_seconds else None
    with get_conn() as conn:
        if cutoff is None:
            rows = conn.execute("SELECT * FROM aviator_rounds WHERE status = 'active'").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM aviator_rounds WHERE status = 'active' AND created_at <= ?", (cutoff,)
            ).fetchall()
    return [dict(r) for r in rows]


def _now_minus_seconds(seconds: int) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def refund_aviator(round_id: int) -> dict | None:
    """Force-resolves an abandoned round: flips it 'active' -> 'refunded' (atomic,
    same race-safe pattern as cashout_aviator()/mark_aviator_crashed() -- a real
    cashout or crash landing at the same instant simply wins the race and this
    becomes a no-op) and gives the stake straight back (gems only, NOT gems_earned --
    a refund is not a win). Returns {"user_id", "bet", "chat_id", "message_id", "gems":
    new balance} so the caller can also try to edit the frozen message, or None if
    the round no longer exists or was already resolved by something else."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id, bet, chat_id, message_id, status FROM aviator_rounds WHERE id = ?", (round_id,)
        ).fetchone()
        if row is None or row["status"] != "active":
            return None
        cur = conn.execute(
            "UPDATE aviator_rounds SET status = 'refunded' WHERE id = ? AND status = 'active'",
            (round_id,),
        )
        if cur.rowcount == 0:
            return None
        conn.execute("UPDATE users SET gems = gems + ? WHERE telegram_id = ?", (row["bet"], row["user_id"]))
        new_gems = conn.execute(
            "SELECT gems FROM users WHERE telegram_id = ?", (row["user_id"],)
        ).fetchone()["gems"]
    return {
        "user_id": row["user_id"], "bet": row["bet"],
        "chat_id": row["chat_id"], "message_id": row["message_id"],
        "gems": new_gems,
    }


GEM_MINING_DURATION_SECONDS = 60 * 60  # 60 minutes per cycle
GEM_MINING_REWARD_GEMS = 25


def get_gem_mining_status(user_id: int) -> dict:
    """Current state of the repeatable gem-mining timer, for the client to render the
    button/countdown on load without needing to press anything. 'active' means a cycle
    is running (started_at is set); 'ready' means that cycle's 60 minutes are up and
    pressing the button will collect it. seconds_left is 0 once ready or if inactive."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT gem_mining_started_at FROM users WHERE telegram_id = ?", (user_id,)
        ).fetchone()
    if row is None or row["gem_mining_started_at"] is None:
        return {"active": False, "ready": False, "seconds_left": 0}
    started = _parse_utc(row["gem_mining_started_at"])
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    if elapsed >= GEM_MINING_DURATION_SECONDS:
        return {"active": True, "ready": True, "seconds_left": 0}
    return {"active": True, "ready": False, "seconds_left": int(GEM_MINING_DURATION_SECONDS - elapsed)}


def collect_gem_mining(user_id: int) -> dict:
    """The gem-mining button's one action, callable any time: if no cycle is running,
    starts one (0 gems collected). If a cycle is running and its 60 minutes are up,
    credits GEM_MINING_REWARD_GEMS and immediately starts the next cycle — so pressing
    the button both collects and restarts in one tap, repeatable indefinitely. If a
    cycle is running but not yet ready, this is a no-op (a well-behaved client keeps
    the button disabled during the countdown) — returns the current status unchanged.
    Returns {'claimed': 0|GEM_MINING_REWARD_GEMS, 'active', 'ready', 'seconds_left'}."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT gem_mining_started_at FROM users WHERE telegram_id = ?", (user_id,)
        ).fetchone()
        now = _now()
        if row is None:
            return {"claimed": 0, "active": False, "ready": False, "seconds_left": 0}
        started_raw = row["gem_mining_started_at"]
        if started_raw is None:
            conn.execute(
                "UPDATE users SET gem_mining_started_at = ? WHERE telegram_id = ?", (now, user_id)
            )
            return {"claimed": 0, "active": True, "ready": False, "seconds_left": GEM_MINING_DURATION_SECONDS}
        elapsed = (datetime.now(timezone.utc) - _parse_utc(started_raw)).total_seconds()
        if elapsed < GEM_MINING_DURATION_SECONDS:
            return {
                "claimed": 0, "active": True, "ready": False,
                "seconds_left": int(GEM_MINING_DURATION_SECONDS - elapsed),
            }
        conn.execute(
            "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ?, "
            "gem_mining_started_at = ? WHERE telegram_id = ?",
            (GEM_MINING_REWARD_GEMS, GEM_MINING_REWARD_GEMS, now, user_id),
        )
        return {
            "claimed": GEM_MINING_REWARD_GEMS, "active": True, "ready": False,
            "seconds_left": GEM_MINING_DURATION_SECONDS,
        }


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


def add_card_to_catalog(filename: str, name: str | None = None, rarity: str = "silver") -> int:
    """Register one image file in the catalog. Call this once per image you drop into static/cards/.
    rarity is passed explicitly (defaulting to "silver") rather than left to the column's DEFAULT,
    since older production DBs still carry a stale DEFAULT of 'rare' from before the silver/gold/
    platinum/diamond tier rename — relying on it silently mislabels every new card."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO cards (filename, name, rarity, is_active, created_at) VALUES (?, ?, ?, 1, ?)",
            (filename, name, rarity, _now()),
        )
        return cur.lastrowid



# ---------- Collections ("Альбомы") ----------
# Each entry is one themed collection: a name/icon plus the exact card filenames
# (matched against cards.filename) that make it up. A filename with no matching
# catalog row is silently skipped by _seed_collections() rather than raising, so
# this list can be edited freely without risk of crashing on a typo.
COLLECTIONS_SEED = [
    {
        "key": "car_lover",
        "name": "Автолюбитель",
        "icon": "🚗",
        "filenames": [
            "audi_cabriolet.jpg", "audi_rs6_avant.jpg",
            "bmw_e36.jpg", "bmw_m3.jpg", "bmw_m5.jpg",
            "bugati_chiron.jpg", "bugatti_brouillard.jpg", "bugatti_la_noire.jpg",
            "car_restomod.jpg", "carmagedon.jpg",
            "cybertruck.jpg", "ford_mustang_gt.jpg", "gelandewagen.jpg",
            "honda_civic.jpg", "low_rider.jpg", "mercedes.jpg",
            "nissan_skyline.jpg", "pagani_barchetta.jpg", "porsche_911.jpg",
            "rr_amethyst.jpg", "rr_arcadia.jpg", "rr_boat_tail.jpg",
            "rr_la_rose.jpg", "rr_sweptail.jpg",
            "tesla model 3.jpg", "toyota_supra.jpg",
            # removed as off-theme (not actually cars, despite the filename):
            # cadillacs_dino.jpg (a "Cadillacs and Dinosaurs" ARCADE CABINET),
            # retro_cars.jpg (a group of 3 toy cars, not a single car),
            # tesla_mini.jpg (a "Tesla Mini" amplifier/gadget, not a car)
        ],
    },
    {
        "key": "cs_weapons",
        "name": "Оружие из CS",
        "icon": "🔫",
        # Every filename here was opened and visually checked (not just name-matched) --
        # several "obvious" candidates turned out to be characters/unrelated items and
        # were left out: cs.jpg/terrorist.jpg/standoff.jpg (player figures, not weapons),
        # scoprion.jpg (Mortal Kombat's Scorpion), spike.jpg (a cactus mascot),
        # colt.jpg (a Brawl Stars character), interchange.jpg (a textile pillow),
        # desert_eye.jpg (an Egyptian amulet), marengo_sword.jpg (a Napoleonic sabre,
        # no CS branding), custom_blade.jpg/firearm_engrave.jpg (real-world hunting
        # knife/shotgun, no CS styling), revolver.jpg (a generic old-west revolver, not
        # CS's R8), vice.jpg/crimson_kimono.jpg (CS glove skins -- not weapons).
        "filenames": [
            "ak-47.jpg", "ak_47.jpg",
            "awp.jpg", "awp_asiimov.jpg", "awp_gungnir.jpg", "awp_medusa.jpg",
            "beretta.jpg", "blaze.jpg", "case_hardened.jpg",
            "desert_eagle.jpg", "dragon_lore.jpg", "fire_serpent.jpg",
            "glock.jpg", "glock_18_fade.jpg", "hyper_beast.jpg", "kerambit.jpg",
            "kill_confirmed.jpg", "knife_1.5.jpg", "m16.jpg", "m4a1_s.jpg", "m4a4.jpg",
            "mp5.jpg", "neo_noir.jpg", "printstream.jpg", "redline.jpg", "uzi.jpg",
            "wild_lotus.jpg",
            # removed: balisong.jpg -- not actually right for this collection
        ],
    },
]


def _seed_collections(conn: sqlite3.Connection) -> None:
    """Idempotent (INSERT OR IGNORE on unique keys) -- safe to call on every
    request. Cards are matched by filename against the live catalog. Also prunes
    any previously-seeded card that's no longer in a collection's filename list
    (e.g. one that turned out to be off-theme and got removed) -- along with any
    player placements pointing at it, so a stale slot never lingers."""
    for coll in COLLECTIONS_SEED:
        conn.execute(
            "INSERT OR IGNORE INTO collections (key, name, icon, created_at) VALUES (?, ?, ?, ?)",
            (coll["key"], coll["name"], coll["icon"], _now()),
        )
        coll_row = conn.execute("SELECT id FROM collections WHERE key = ?", (coll["key"],)).fetchone()
        coll_id = coll_row["id"]
        wanted_card_ids = set()
        for fn in coll["filenames"]:
            card_row = conn.execute("SELECT id FROM cards WHERE filename = ?", (fn,)).fetchone()
            if card_row is None:
                continue
            wanted_card_ids.add(card_row["id"])
            conn.execute(
                "INSERT OR IGNORE INTO collection_cards (collection_id, card_id) VALUES (?, ?)",
                (coll_id, card_row["id"]),
            )
        existing_ids = {
            row["card_id"] for row in conn.execute(
                "SELECT card_id FROM collection_cards WHERE collection_id = ?", (coll_id,)
            ).fetchall()
        }
        for stale_id in existing_ids - wanted_card_ids:
            conn.execute(
                "DELETE FROM collection_cards WHERE collection_id = ? AND card_id = ?",
                (coll_id, stale_id),
            )
            conn.execute(
                "DELETE FROM user_collection_cards WHERE collection_id = ? AND card_id = ?",
                (coll_id, stale_id),
            )


def _maybe_complete_collection(conn: sqlite3.Connection, user_id: int, collection_id: int) -> bool:
    """Returns True if every slot is now placed and this call is what just
    recorded the completion achievement for the first time."""
    already = conn.execute(
        "SELECT id FROM user_collection_completions WHERE user_id = ? AND collection_id = ?",
        (user_id, collection_id),
    ).fetchone()
    if already is not None:
        return False
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM collection_cards WHERE collection_id = ?", (collection_id,)
    ).fetchone()["n"]
    placed = conn.execute(
        "SELECT COUNT(*) AS n FROM user_collection_cards WHERE user_id = ? AND collection_id = ?",
        (user_id, collection_id),
    ).fetchone()["n"]
    if total > 0 and placed >= total:
        conn.execute(
            "INSERT OR IGNORE INTO user_collection_completions (user_id, collection_id, completed_at) VALUES (?, ?, ?)",
            (user_id, collection_id, _now()),
        )
        return True
    return False


def get_collections_overview(user_id: int) -> list[dict]:
    """List of every defined collection with this player's progress -- for the
    collections list screen opened from the "Коллекция" button in Профиль."""
    with get_conn() as conn:
        _seed_collections(conn)
        colls = conn.execute("SELECT id, key, name, icon FROM collections ORDER BY id").fetchall()
        out = []
        for c in colls:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM collection_cards WHERE collection_id = ?", (c["id"],)
            ).fetchone()["n"]
            placed = conn.execute(
                "SELECT COUNT(*) AS n FROM user_collection_cards WHERE user_id = ? AND collection_id = ?",
                (user_id, c["id"]),
            ).fetchone()["n"]
            completed_row = conn.execute(
                "SELECT completed_at FROM user_collection_completions WHERE user_id = ? AND collection_id = ?",
                (user_id, c["id"]),
            ).fetchone()
            out.append({
                "id": c["id"], "key": c["key"], "name": c["name"], "icon": c["icon"],
                "total": total, "placed": placed,
                "completed": completed_row is not None,
            })
        return out


def get_collection_detail(user_id: int, collection_id: int) -> dict:
    """Per-slot detail for one collection: for each member card, whether this
    player owns at least one copy (unlocks the "+" to place it) and whether
    they've already placed it (fills the slot permanently)."""
    with get_conn() as conn:
        _seed_collections(conn)
        coll = conn.execute("SELECT id, key, name, icon FROM collections WHERE id = ?", (collection_id,)).fetchone()
        if coll is None:
            raise ValueError("collection not found")
        card_rows = conn.execute(
            "SELECT c.id, c.filename, c.name, c.rarity FROM collection_cards cc "
            "JOIN cards c ON c.id = cc.card_id WHERE cc.collection_id = ? ORDER BY c.id",
            (collection_id,),
        ).fetchall()
        owned_ids = {
            row["card_id"] for row in conn.execute(
                "SELECT DISTINCT cc.card_id AS card_id FROM collection_cards cc "
                "JOIN user_cards uc ON uc.card_id = cc.card_id AND uc.user_id = ? "
                "WHERE cc.collection_id = ?",
                (user_id, collection_id),
            ).fetchall()
        }
        placed_ids = {
            row["card_id"] for row in conn.execute(
                "SELECT card_id FROM user_collection_cards WHERE user_id = ? AND collection_id = ?",
                (user_id, collection_id),
            ).fetchall()
        }
        cards = [
            {
                "card_id": r["id"], "filename": r["filename"], "name": r["name"],
                "rarity": r["rarity"], "owned": r["id"] in owned_ids, "placed": r["id"] in placed_ids,
            }
            for r in card_rows
        ]
        _maybe_complete_collection(conn, user_id, collection_id)
        completed_row = conn.execute(
            "SELECT completed_at FROM user_collection_completions WHERE user_id = ? AND collection_id = ?",
            (user_id, collection_id),
        ).fetchone()
        return {
            "id": coll["id"], "key": coll["key"], "name": coll["name"], "icon": coll["icon"],
            "cards": cards, "completed": completed_row is not None,
        }


def place_collection_card(user_id: int, collection_id: int, card_id: int) -> dict:
    """Marks one collection slot as filled -- purely a completion-tracking action,
    the card itself is never touched/locked/consumed (the same copy stays fully
    usable for market/PvP/staking). Requires owning at least one copy of the card.
    Idempotent -- placing an already-placed slot again is a harmless no-op."""
    with get_conn() as conn:
        member = conn.execute(
            "SELECT 1 FROM collection_cards WHERE collection_id = ? AND card_id = ?",
            (collection_id, card_id),
        ).fetchone()
        if member is None:
            raise ValueError("card is not part of this collection")
        owns = conn.execute(
            "SELECT 1 FROM user_cards WHERE user_id = ? AND card_id = ? LIMIT 1",
            (user_id, card_id),
        ).fetchone()
        if owns is None:
            raise ValueError("you don't own this card")
        conn.execute(
            "INSERT OR IGNORE INTO user_collection_cards (user_id, collection_id, card_id, placed_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, collection_id, card_id, _now()),
        )
        newly_completed = _maybe_complete_collection(conn, user_id, collection_id)
        return {"placed": True, "newly_completed": newly_completed}


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
RARITY_WEIGHTS = {"bronze": 50, "silver": 29, "gold": 16.5, "platinum": 4, "diamond": 0.5}


def draw_random_card() -> sqlite3.Row | None:
    """Pick one active card, weighted by rarity (RARITY_WEIGHTS) — most drops are BRONZE,
    then SILVER, GOLD and PLATINUM progressively less common. Returns None if the catalog is empty.
    Falls back gracefully (equal weight) if a tier has no active cards yet."""
    return _draw_card_weighted(RARITY_WEIGHTS)


def _draw_card_weighted(weights: dict[str, float]) -> sqlite3.Row | None:
    """Shared weighted-draw helper — pick one active card, weighted by the given
    per-rarity weights dict. Used by draw_random_card() (RARITY_WEIGHTS) and the
    gem-cases (each case has its own, richer odds). Falls back to equal weight
    across whatever tiers exist if none of them have a positive weight."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM cards WHERE is_active = 1").fetchall()
        if not rows:
            return None
        by_rarity: dict[str, list] = {}
        for r in rows:
            by_rarity.setdefault(r["rarity"] or "silver", []).append(r)
        tiers = [t for t in by_rarity if weights.get(t, 0) > 0]
        if tiers:
            tier_weights = [weights[t] for t in tiers]
        else:
            tiers = list(by_rarity.keys())
            tier_weights = [1 for _ in tiers]
        chosen_tier = random.choices(tiers, weights=tier_weights, k=1)[0]
        return random.choice(by_rarity[chosen_tier])


CASE_DEFS = {
    # Each case draws from ONLY its two adjacent target tiers — no exposure to the other
    # three rarities at all. Per 100 gems spent, every case yields MORE of its named target
    # tier than farm does at the same gem spend, by a deliberately modest, graduated margin
    # (Hamster +10% silver, Duck +15% gold, Capybara +20% platinum, Pepe +25% diamond vs
    # RARITY_WEIGHTS/FARM_COST_GEMS=25), so a player chasing that specific tier is always a
    # bit better off buying the matching case than farming for it. The tradeoff: a case
    # gives fewer total cards per gem than farm (1 card per open vs farm's cheaper/faster
    # draws) and zero chance at anything outside its two tiers, unlike farm's small tail
    # chance at every rarity.
    "hamster": {"name": "Хомяк", "price": 50, "image": "case/case_hamster.jpg",  # Bronze/Silver only, +10% silver vs farm
                "weights": {"bronze": 36, "silver": 64}},
    "duck": {"name": "Уточка", "price": 100, "image": "case/case_utya.jpg",  # Silver/Gold only, +15% gold vs farm
             "weights": {"silver": 40, "gold": 60}},
    "capybara": {"name": "Капибара", "price": 200, "image": "case/case_capybara.jpg",  # Gold/Platinum only, +20% platinum vs farm
                 "weights": {"gold": 33, "platinum": 67}},
    "pepe": {"name": "Пепе", "price": 500, "image": "case/case_pep.jpg",  # Platinum/Diamond only, +25% diamond vs farm
             "weights": {"platinum": 75, "diamond": 25}},
}


class CaseNotFound(Exception):
    """Raised by open_case() when case_key isn't in CASE_DEFS."""


def open_case(user_id: int, case_key: str) -> dict:
    """Spends the case's gem price for one random card, weighted by that case's own
    (better-than-farm) odds. Raises CaseNotFound for a bad key, InsufficientGems if the
    balance check fails (checked and deducted atomically, same pattern as farm())."""
    case_def = CASE_DEFS.get(case_key)
    if case_def is None:
        raise CaseNotFound()
    card = _draw_card_weighted(case_def["weights"])
    if card is None:
        raise ValueError("catalog is empty")
    with get_conn() as conn:
        row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if row is None or row["gems"] < case_def["price"]:
            raise InsufficientGems()
        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (case_def["price"], user_id))
        cur = conn.execute(
            "INSERT INTO user_cards (user_id, card_id, obtained_at) VALUES (?, ?, ?)",
            (user_id, card["id"], _now()),
        )
        user_card_id = cur.lastrowid
        drop_number = conn.execute("SELECT COUNT(*) FROM user_cards").fetchone()[0]
        if not (ADMIN_ID and str(user_id) == str(ADMIN_ID)):
            _bump_counter(conn, "case_open")
    return {
        "user_card_id": user_card_id,
        "card_id": card["id"],
        "filename": card["filename"],
        "name": card["name"],
        "rarity": card["rarity"],
        "drop_number": drop_number,
    }


class CardGiveawayError(Exception):
    """Raised by create_card_giveaway() when the admin has no matching cards to give
    away, or there's no one to give them to."""


def create_card_giveaway(admin_id: int, rarity: str, total_cards: int,
                          min_per_winner: int = 1, max_per_winner: int = 5) -> dict:
    """Instantly gives away up to total_cards of the ADMIN'S OWN owned cards of the
    given rarity (skipping any that are busy — staked, listed for sale/swap, or in a
    PvP round) — reassigns ownership in place (UPDATE user_cards.user_id) rather than
    minting new ones, so the admin's own collection really does shrink by what's given
    away. Winners are drawn by shuffling every OTHER registered user and handing out a
    random min_per_winner..max_per_winner chunk per pick, looping back through a fresh
    shuffle of the same participant list as many times as needed until the card pool
    (or the requested total_cards, whichever is smaller) runs out — so a small pool of
    participants naturally ends up sharing everything, one chunk at a time. Runs and
    resolves immediately, no waiting window. Returns {"winners": [{"telegram_id",
    "username", "first_name", "count"}, ...], "total_distributed": int}."""
    with get_conn() as conn:
        pool_rows = conn.execute(
            "SELECT uc.id FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
            "WHERE uc.user_id = ? AND c.rarity = ? "
            "AND uc.listed_price IS NULL AND uc.swap_listed = 0 "
            "AND uc.staked_at IS NULL AND uc.pvp_round_id IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM number_giveaways ng WHERE ng.user_card_id = uc.id AND ng.drawn_at IS NULL)",
            (admin_id, rarity),
        ).fetchall()
        pool_ids = [r["id"] for r in pool_rows]
        if not pool_ids:
            raise CardGiveawayError(f"нет доступных карт редкости {rarity} в твоём профиле")
        random.shuffle(pool_ids)
        pool_ids = pool_ids[:total_cards]

        participants = conn.execute(
            "SELECT telegram_id, username, first_name FROM users WHERE telegram_id != ?",
            (admin_id,),
        ).fetchall()
        if not participants:
            raise CardGiveawayError("нет участников (в боте кроме тебя никого нет)")
        participants = list(participants)

        winners: dict[int, dict] = {}
        shuffled = random.sample(participants, len(participants))
        pos = 0
        while pool_ids:
            if pos >= len(shuffled):
                shuffled = random.sample(participants, len(participants))
                pos = 0
            p = shuffled[pos]
            pos += 1
            amount = min(random.randint(min_per_winner, max_per_winner), len(pool_ids))
            given_ids = [pool_ids.pop() for _ in range(amount)]
            placeholders = ",".join("?" for _ in given_ids)
            for gid in given_ids:
                _detach_number_on_card_transfer(conn, gid)
            conn.execute(
                f"UPDATE user_cards SET user_id = ?, listed_price = NULL, swap_listed = 0, "
                f"staked_at = NULL, pvp_round_id = NULL, number_override = NULL, pinned_at = NULL WHERE id IN ({placeholders})",
                (p["telegram_id"], *given_ids),
            )
            w = winners.setdefault(p["telegram_id"], {
                "telegram_id": p["telegram_id"], "username": p["username"],
                "first_name": p["first_name"], "count": 0,
            })
            w["count"] += amount

    return {
        "winners": sorted(winners.values(), key=lambda w: -w["count"]),
        "total_distributed": sum(w["count"] for w in winners.values()),
    }


# Burn: sacrifice several owned cards of one rarity for a GUARANTEED shot at exactly the
# next tier up (unlike craft, which is a random spread across same-tier-or-higher). Free —
# no gem cost, since craft already covers the "pay gems, random result" niche. Success chance
# (BURN_SUCCESS_RATE) depends on the target tier — evolving into something rarer is riskier:
# 99% into silver, 98% into gold, 96% into platinum, 92% into diamond. The rest of the time
# everything burned is lost for nothing, so it's a real risk.
# Counts scale with how much scarcer the target tier actually is in RARITY_WEIGHTS (the
# base farm odds, untouched): bronze->silver/silver->gold/gold->platinum are all a ~1.7-2.2x
# scarcity step so they cost close to the same; platinum->diamond is a genuinely bigger
# scarcity jump (~7x in raw farm odds) so it costs the most — but capped well below a literal
# 1:1 mapping to that ratio (which would need ~15-18 platinum cards) since platinum itself is
# already hard to farm and that would make the top tier unreachable via burn.
BURN_REQUIREMENTS = {
    # Uniform 4 cards at every tier now (was 4/5/5/6) -- simpler to remember, and at the
    # unchanged BURN_SUCCESS_RATE this puts Platinum->Diamond at ~1420 gems/Diamond
    # (buying the 4 expected Platinum cards via the cheapest source, Capybara).
    "bronze":   {"target": "silver",   "count": 4},
    "silver":   {"target": "gold",     "count": 4},
    "gold":     {"target": "platinum", "count": 4},
    "platinum": {"target": "diamond",  "count": 4},
    # No "diamond" entry — Diamond is the top tier, nothing to burn UP into (Diamond can
    # still be re-rolled via craft_card(), which is a different mechanic).
}

# Failure chance scales with how rare/valuable the TARGET tier is — evolving into something
# higher up is riskier. Keyed by target_rarity (not source rarity).
BURN_SUCCESS_RATE = {
    "silver":   0.98,  # 2% fail
    "gold":     0.96,  # 4% fail
    "platinum": 0.92,  # 8% fail
    "diamond":  0.84,  # 16% fail
}


class BurnNotEnoughCards(Exception):
    """Raised by burn_cards() when the user doesn't own enough non-busy cards of the
    requested rarity."""


class BurnNotAllowed(Exception):
    """Raised by burn_cards() for a rarity with no burn recipe (diamond — already the top
    tier, nothing to burn up into)."""


def burn_cards(user_id: int, rarity: str, user_card_ids: list[int]) -> dict:
    """Burns exactly the BURN_REQUIREMENTS[rarity]['count'] cards the player themselves
    picked (user_card_ids) — free, no gem cost. Every id must belong to user_id, match the
    given rarity, and not be busy (staked/listed/in a swap or PvP round); anything else
    (wrong count, someone else's card, a duplicate id, a busy card) raises
    BurnNotEnoughCards rather than silently dropping/substituting cards, since the player
    chose these specific ones. BURN_SUCCESS_RATE[target_rarity] of the time the burned cards
    are replaced with one freshly-drawn card of the next-tier-up rarity (99%/98%/96%/92% for
    silver/gold/platinum/diamond); the rest of the time NOTHING comes back — a genuine loss,
    not just flavor text.

    Like craft_card(), this NEVER actually DELETEs a user_cards row — market_offers,
    swap_offers, swap_offer_cards, and pvp_entries all keep permanent FK-referencing
    history rows, so a hard DELETE on any card that was EVER listed/swapped/staked/PvP'd
    (even long ago, even if not currently busy) blows up with "FOREIGN KEY constraint
    failed". Instead: on success, one burned row is reused in place for the new card
    (exactly like craft_card()) and the rest are marked voided=1; on failure, all of them
    are marked voided=1. voided rows are excluded from get_inventory(), get_total_farmed(),
    and get_leaderboard() — so "Всего" genuinely goes down — but physically stay put so
    every historical reference stays valid. Raises BurnNotAllowed for an unrecognized/
    top-tier rarity."""
    recipe = BURN_REQUIREMENTS.get(rarity)
    if recipe is None:
        raise BurnNotAllowed()
    target_rarity, count = recipe["target"], recipe["count"]
    if len(user_card_ids) != count or len(set(user_card_ids)) != count:
        raise BurnNotEnoughCards()

    with get_conn() as conn:
        placeholders = ",".join("?" for _ in user_card_ids)
        rows = conn.execute(
            f"SELECT uc.id FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
            f"WHERE uc.id IN ({placeholders}) AND uc.user_id = ? AND c.rarity = ? "
            f"AND uc.listed_price IS NULL AND uc.swap_listed = 0 "
            f"AND uc.staked_at IS NULL AND uc.pvp_round_id IS NULL AND uc.voided = 0 "
            f"AND uc.pinned_at IS NULL "
            f"AND NOT EXISTS (SELECT 1 FROM number_giveaways ng WHERE ng.user_card_id = uc.id AND ng.drawn_at IS NULL)",
            (*user_card_ids, user_id, rarity),
        ).fetchall()
        if len(rows) != count:
            raise BurnNotEnoughCards()
        burn_ids = [r["id"] for r in rows]

        success = random.random() < BURN_SUCCESS_RATE[target_rarity]
        new_card = _draw_card_weighted({target_rarity: 100}) if success else None

        # Counts every evolve ATTEMPT (press), win or lose -- matches how farm/case_open
        # count every press, so /admin's usage numbers are comparable across features
        # instead of undercounting evolve by however often the risk roll fails.
        if not (ADMIN_ID and str(user_id) == str(ADMIN_ID)):
            _bump_counter(conn, "evolve")

        new_user_card_id = None
        new_drop_number = None
        if new_card is not None:
            # Reuse the first burned row in place for the new card (same trick as
            # craft_card()) — keeps its id/history valid instead of touching FK-sensitive
            # rows unnecessarily. Its old number (natural or bought) is about to stop
            # being shown by anyone — free it into the numbers marketplace first.
            recipient_id = burn_ids[0]
            _free_number(conn, recipient_id)
            new_obtained_at = _now()
            conn.execute(
                "UPDATE user_cards SET card_id = ?, obtained_at = ?, listed_price = NULL, "
                "swap_listed = 0, staked_at = NULL, pvp_round_id = NULL, voided = 0, number_override = NULL, pinned_at = NULL WHERE id = ?",
                (new_card["id"], new_obtained_at, recipient_id),
            )
            new_user_card_id = recipient_id
            new_drop_number = conn.execute(
                "SELECT COUNT(*) FROM user_cards WHERE obtained_at <= ?",
                (new_obtained_at,),
            ).fetchone()[0]
            remaining_ids = burn_ids[1:]
        else:
            remaining_ids = burn_ids

        if remaining_ids:
            # These cards are genuinely destroyed (voided) — their numbers/names go
            # back to whoever owned them (never left dangling or released to the
            # public), see _free_number()/_release_name_on_card_void().
            for rid in remaining_ids:
                _free_number(conn, rid)
                _release_name_on_card_void(conn, rid)
            placeholders = ",".join("?" for _ in remaining_ids)
            conn.execute(
                f"UPDATE user_cards SET voided = 1, listed_price = NULL, swap_listed = 0, "
                f"staked_at = NULL, pvp_round_id = NULL, number_override = NULL, pinned_at = NULL, "
                f"custom_name = NULL, custom_rarity = NULL WHERE id IN ({placeholders})",
                remaining_ids,
            )

        total_farmed = conn.execute("SELECT COUNT(*) AS n FROM user_cards WHERE voided = 0").fetchone()["n"]

    result = {
        "success": success,
        "burned_count": count,
        "rarity": rarity,
        "target_rarity": target_rarity,
        "total_farmed": total_farmed,
    }
    if new_card is not None:
        result["new_card"] = {
            "user_card_id": new_user_card_id,
            "card_id": new_card["id"],
            "filename": new_card["filename"],
            "name": new_card["name"],
            "rarity": new_card["rarity"],
            "drop_number": new_drop_number,
        }
    return result


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
    farms fired in quick succession can't both spend the same last few gems). Every farm
    (including a brand new account's very first ones) draws from the same RARITY_WEIGHTS --
    the old undisclosed new-player boost on the first 2 farms was removed since it was
    landing high rarities for newbies too often."""
    card = _draw_card_weighted(RARITY_WEIGHTS)
    if card is None:
        return None
    with get_conn() as conn:
        row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if row is None or row["gems"] < FARM_COST_GEMS:
            raise InsufficientGems()
        prior_cards = conn.execute(
            "SELECT COUNT(*) FROM user_cards WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
        conn.execute(
            "UPDATE users SET gems = gems - ?, last_farm_at = ? WHERE telegram_id = ?",
            (FARM_COST_GEMS, _now(), user_id),
        )
        cur = conn.execute(
            "INSERT INTO user_cards (user_id, card_id, obtained_at) VALUES (?, ?, ?)",
            (user_id, card["id"], _now()),
        )
        user_card_id = cur.lastrowid
        if not (ADMIN_ID and str(user_id) == str(ADMIN_ID)):
            _bump_counter(conn, "farm")
        # Global drop number — position among ALL cards ever farmed/crafted by ANY user,
        # not just this user's own collection.
        drop_number = conn.execute("SELECT COUNT(*) FROM user_cards").fetchone()[0]

        # Anti-bot referral payout: requires BOTH a first-ever farm (checked here) AND
        # having joined PUBLIC_CHAT (chat_member_verified -- see mark_chat_verified()).
        # This only pays out right here if chat membership somehow got verified before
        # this player's first farm (rare -- the usual order is farm first, chat-join
        # confirmed later by bot.py's periodic getChatMember check). Otherwise the
        # reward stays queued in ref_reward_pending and mark_chat_verified() is what
        # fires it once joining the chat is the last box left to tick.
        referral_reward = None
        if prior_cards == 0:
            me = conn.execute(
                "SELECT ref_by, ref_reward_pending, chat_member_verified FROM users WHERE telegram_id = ?",
                (user_id,),
            ).fetchone()
            if me["ref_by"] is not None and me["ref_reward_pending"] and me["chat_member_verified"]:
                reward_amount = me["ref_reward_pending"]
                conn.execute(
                    "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? WHERE telegram_id = ?",
                    (reward_amount, reward_amount, me["ref_by"]),
                )
                conn.execute("UPDATE users SET ref_reward_pending = 0 WHERE telegram_id = ?", (user_id,))
                referral_reward = {"referrer_id": me["ref_by"], "amount": reward_amount}

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


# Craft cost scales with the rarity of the card being burned — crafting a Diamond (a
# near-lateral reroll, since it's already the top tier) costs far more than crafting a
# cheap Bronze. Keeps craft from being a flat-rate gem sink regardless of what's at stake.
CRAFT_COST_GEMS = {
    # Reverted back to the cheap flat scale (per request, again) -- 25/25/50/100/200.
    "bronze": 25,
    "silver": 25,
    "gold": 50,
    "platinum": 100,
    "diamond": 200,
}
TRANSFER_FEE_GEMS = 25  # charged to the sender for a direct @username gift
# Minimum gems a card can be listed/offered for on the market, scaled by rarity — a
# pricier/rarer tier gets a higher floor. Applies to both a seller's listing price and a
# buyer's offer (list_card()/make_offer()).
MIN_LISTING_PRICE_BY_RARITY = {"bronze": 25, "silver": 50, "gold": 100, "platinum": 100, "diamond": 100}


def get_min_listing_price(rarity: str | None) -> int:
    """The market price floor for this rarity — falls back to the bronze floor for an
    unrecognized/missing rarity."""
    return MIN_LISTING_PRICE_BY_RARITY.get(rarity or "bronze", MIN_LISTING_PRICE_BY_RARITY["bronze"])


def get_craft_cost(rarity: str | None) -> int:
    """CRAFT_COST_GEMS for this rarity — falls back to the bronze cost for an
    unrecognized/missing rarity."""
    return CRAFT_COST_GEMS.get(rarity or "bronze", CRAFT_COST_GEMS["bronze"])


class ListingPriceTooLow(Exception):
    """Raised by list_card()/make_offer() when price_gems is below get_min_listing_price()
    for that specific card's rarity — carries the required minimum so the caller (api.py)
    can report the exact number back to the player."""
    def __init__(self, min_price: int):
        self.min_price = min_price
        super().__init__(f"minimum price is {min_price} gems")

# Craft: burn one owned card + CRAFT_COST_GEMS gems for one new random card. Odds depend on
# the tier of the card being burned — burning a higher tier gives much better odds at another
# high tier (mirrors most collector-game "upgrade" mechanics). Each row must sum to 100.
CRAFT_WEIGHTS = {
    # Crafting never produces a tier BELOW the one burned — only same-tier-or-higher is
    # possible (burn Gold, get Gold/Platinum/Diamond only, never Bronze/Silver). Each row
    # only lists the tiers it can actually produce and must sum to 100.
    #
    # Every non-self column here is still >= the equivalent free-farm chance (RARITY_WEIGHTS:
    # silver 29/gold 13/platinum 7/diamond 1), so paying gems + a card never leaves you WORSE
    # off than just farming for free.
    #
    # But that alone isn't enough to balance CRAFT_COST_GEMS against what you get back: valuing
    # every card at its farm-equivalent gem cost (what it'd take to farm one from scratch —
    # bronze ~50, silver ~86, gold ~192, platinum ~357, diamond ~2500), the OLD rows here paid
    # back 175-245% of what you put in (gems + the burned card) — craft was a strictly better
    # deal than farming or buying cases at every tier, which made it the only rational way to
    # play. These rows instead land each tier at a modest 90-180% return: still always a good
    # trade (never worse than farm), but not a free-money loop.
    #
    # Just beating farm's OWN per-tier percentages isn't enough either: the tiers ABOVE the one
    # being crafted must have STRICTLY higher combined odds here than in RARITY_WEIGHTS, not
    # just equal — equal odds plus an extra burned card is a worse deal than just farming again,
    # which the very first cut of this rebalance missed for bronze and silver (0 premium).
    # Diamond input is the one deliberate exception — it's meant to be a real sink/risk (see
    # CRAFT_DIAMOND_SUCCESS_RATE), so it gets no "beat the odds" treatment at all.
    "bronze":   {"bronze": 70, "silver": 16, "gold": 8, "platinum": 5, "diamond": 1},
    "silver":   {"silver": 76, "gold": 13, "platinum": 8, "diamond": 3},
    "gold":     {"gold": 80, "platinum": 10, "diamond": 10},
    "platinum": {"platinum": 50, "diamond": 50},
    # Diamond is the top tier — nowhere higher to go, so crafting one just re-rolls
    # another random Diamond (people reroll for a different Diamond card they want more).
    "diamond": {"diamond": 100},
}

# Diamond craft is risky: instead of a guaranteed reroll, there's a
# CRAFT_DIAMOND_SUCCESS_RATE chance of getting a new Diamond card and a (1 - rate) chance
# the card is destroyed outright. Gems are still spent either way.
CRAFT_DIAMOND_SUCCESS_RATE = 0.95


class CraftNotOwned(Exception):
    """Raised by craft_card() when user_card_id doesn't exist or doesn't belong to this user."""


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
    """Burns one owned card (must belong to user_id) for that card's CRAFT_COST_GEMS[rarity] gems and one new
    random card, drawn with odds skewed by the burned card's rarity. The new card is inserted
    with the exact same obtained_at as the burned one, so it inherits its collection number
    (drop_number) instead of jumping to the end of the list. Diamond input is special: instead
    of a guaranteed reroll, there's only a CRAFT_DIAMOND_SUCCESS_RATE chance of getting a new
    Diamond card back — the rest of the time the card is destroyed outright (gems are still
    spent either way, same as burn_cards()'s risk mechanic). Raises InsufficientGems if the
    gem balance is too low, CraftNotOwned if user_card_id isn't this user's."""
    with get_conn() as conn:
        owned = conn.execute(
            "SELECT uc.id, uc.obtained_at, uc.listed_price, uc.swap_listed, uc.staked_at, uc.pvp_round_id, uc.pinned_at, c.rarity, "
            "(SELECT 1 FROM number_giveaways ng WHERE ng.user_card_id = uc.id AND ng.drawn_at IS NULL) AS in_giveaway "
            "FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
            "WHERE uc.id = ? AND uc.user_id = ?",
            (user_card_id, user_id),
        ).fetchone()
        if owned is None:
            raise CraftNotOwned()
        if owned["listed_price"] is not None or owned["swap_listed"] or owned["staked_at"] is not None or owned["pvp_round_id"] is not None or owned["pinned_at"] is not None or owned["in_giveaway"]:
            raise CraftNotOwned()
        cost = get_craft_cost(owned["rarity"])
        gems_row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if gems_row is None or gems_row["gems"] < cost:
            raise InsufficientGems()

    rarity = owned["rarity"] or "bronze"
    success = random.random() < CRAFT_DIAMOND_SUCCESS_RATE if rarity == "diamond" else True

    new_card = draw_card_for_craft(rarity) if success else None
    if success and new_card is None:
        raise ValueError("catalog is empty")

    with get_conn() as conn:
        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (cost, user_id))
        # Counts every craft ATTEMPT (press) -- only Diamond input can actually fail
        # (CRAFT_DIAMOND_SUCCESS_RATE), everything else always succeeds, but count it
        # here unconditionally anyway so /admin's usage numbers stay comparable.
        if not (ADMIN_ID and str(user_id) == str(ADMIN_ID)):
            _bump_counter(conn, "craft")
        if success:
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

        else:
            # Diamond craft failure — card destroyed outright. Same "voided" soft-destroy
            # trick as burn_cards(): never DELETE (would hit the same FK constraint), just
            # flag it out of totals/inventory/leaderboard while keeping the row (and every
            # historical FK reference to it) intact. Its number/name go back to their owner.
            _free_number(conn, owned["obtained_at"])
            _free_number(conn, user_card_id)
            _release_name_on_card_void(conn, user_card_id)
            conn.execute(
                "UPDATE user_cards SET voided = 1, listed_price = NULL, swap_listed = 0, staked_at = NULL, pvp_round_id = NULL, number_override = NULL, pinned_at = NULL, "
                "custom_name = NULL, custom_rarity = NULL WHERE id = ?",
                (user_card_id,),
            )
            new_user_card_id = None
            drop_number = None
        total_farmed = conn.execute("SELECT COUNT(*) AS n FROM user_cards WHERE voided = 0").fetchone()["n"]

    result = {"success": success, "total_farmed": total_farmed, "cost": cost}
    if success:
        result.update({
            "user_card_id": new_user_card_id,
            "card_id": new_card["id"],
            "filename": new_card["filename"],
            "name": new_card["name"],
            "rarity": new_card["rarity"],
            "drop_number": drop_number,
        })
    return result


# ---------------------------------------------------------------------------
# Card-number auctions — see the card_numbers schema comment for the state machine.
# Minimum bid is flat for every number (not tiered by how low/rare it is): the market
# decides how much a specific number is worth by how high people actually bid it up.
# ---------------------------------------------------------------------------

NUMBER_MIN_BID_GEMS = 25
# The FIRST bid on a number nobody has bid on yet (status still 'free') opens a
# shorter 12h window — no point holding a still-uncontested number open for a full
# day. Once someone else jumps in and it's a real fight (status already 'auction'),
# every further bid keeps resetting the timer to the original longer +24h window —
# an auction already in progress never gets cut short out from under an active bidder.
NUMBER_AUCTION_WINDOW_SECONDS_NEW = 12 * 60 * 60  # first bid on a free number: +12h
NUMBER_AUCTION_WINDOW_SECONDS = 12 * 60 * 60       # every bid after that: +12h (was 24h)


class NumberNotAvailable(Exception):
    """Raised when a number isn't in the state the caller expects — already claimed by
    someone else, not up for auction, not listed for resale, etc."""


class NumberCardNotUsable(Exception):
    """Raised when the target/source user_cards row doesn't belong to that user, is
    voided, or is busy (listed/swapped/staked/in a PvP round)."""


class NumberBidTooLow(Exception):
    """Raised by place_number_bid() when amount_gems doesn't beat the current highest
    bid (or NUMBER_MIN_BID_GEMS, whichever is higher) — carries the minimum that would
    have worked."""
    def __init__(self, min_bid: int):
        self.min_bid = min_bid
        super().__init__(f"minimum bid is {min_bid} gems")


class NumberNotRare(Exception):
    """Raised by extract_card_number() when the card's current number (natural or
    already-pinned) isn't in ALLOWED_AUCTION_NUMBERS — only numbers rare enough to
    matter can be pulled off a card this way."""


def _usable_owned_card(conn: sqlite3.Connection, user_id: int, user_card_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, obtained_at FROM user_cards WHERE id = ? AND user_id = ? AND voided = 0 "
        "AND listed_price IS NULL AND swap_listed = 0 AND staked_at IS NULL AND pvp_round_id IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM number_giveaways ng WHERE ng.user_card_id = user_cards.id AND ng.drawn_at IS NULL)",
        (user_card_id, user_id),
    ).fetchone()


def _finalize_expired_number_auctions(conn: sqlite3.Connection) -> None:
    """Any auction whose 24h window has lapsed with no new bid flips to 'owned' — the
    last bidder wins, gems they already paid stay spent. Called opportunistically at the
    top of every numbers-marketplace read/write, same no-background-job philosophy as
    settle_staking()/PvP resolution."""
    now = _now()
    rows = conn.execute(
        "SELECT number, highest_bidder_id FROM card_numbers WHERE status = 'auction' AND bid_expires_at <= ?",
        (now,),
    ).fetchall()
    for r in rows:
        conn.execute(
            "UPDATE card_numbers SET status = 'owned', owner_id = ?, "
            "highest_bid = NULL, highest_bidder_id = NULL, bid_expires_at = NULL, updated_at = ? "
            "WHERE number = ?",
            (r["highest_bidder_id"], now, r["number"]),
        )


# Repdigit "vanity" numbers — 111, 222, ... 999 — read as more desirable even when no
# card ever actually freed them naturally. seed_vanity_numbers() makes each one available
# ONLY if it isn't currently displayed by a real, live card (checked fresh every call —
# see its docstring), so this never creates a duplicate number.
# Repdigit "vanity" numbers (11, 22, ... 999) plus 67 by special request, and every
# LOW number up to LOW_NUMBER_SEED_UP_TO (1..25) — both get proactively surfaced in
# the marketplace the moment they are not held by any live card, instead of waiting
# for someone to burn/evolve a card that happened to hold one.
VANITY_NUMBERS = [
    11, 22, 33, 44, 55, 66, 67, 69, 77, 88, 99,
    100, 101,
    111, 200, 222, 300, 333, 400, 444, 500, 555, 600, 666,
    700, 777, 800, 888, 900, 999, 1000, 1001,
    # 4-digit repdigits + round thousands, added by request -- same "blatnye"/vanity
    # idea as the shorter numbers above, just extended up to 10 000.
    1111, 2000, 2222, 3000, 3333, 4000, 4444, 5000, 5555,
    6000, 6666, 7000, 7777, 8000, 8888, 9000, 9999, 10000,
]
LOW_NUMBER_SEED_UP_TO = 100  # was 25, extended to every low number 1..100 by request
# The auction/free pool is intentionally curated, not "every number any card ever
# held" — a card being burned/evolved still frees whatever number it had (via
# _free_number, tracked in card_numbers as usual), but the board only shows and the
# bid endpoint only accepts numbers in this exact set: VANITY_NUMBERS plus every low
# number 1..LOW_NUMBER_SEED_UP_TO. Anything else that gets freed just sits untracked
# in the UI — not for sale, by design.
ALLOWED_AUCTION_NUMBERS = frozenset(VANITY_NUMBERS) | frozenset(range(1, LOW_NUMBER_SEED_UP_TO + 1))


def _seed_number_if_unclaimed(conn: sqlite3.Connection, n: int) -> None:
    """Inserts number `n` into card_numbers as 'free' if it ISN'T currently shown by any
    live (non-voided) card (natural or pinned via override) and isn't already tracked
    there. Safe to call every time the board is loaded; idempotent.

    Checks "already tracked in card_numbers" FIRST (a cheap primary-key lookup) before
    the expensive "is some live card currently showing n" scan (a full table scan --
    no index on the computed natural-number expression) -- once a number has been
    seeded once (free/auction/owned, doesn't matter which), every later board load can
    skip the expensive check for it entirely. Before this reordering, seed_vanity_numbers
    re-ran the expensive scan for EVERY candidate on EVERY board load forever, which is
    exactly what made the Numbers screen take ~5s to open after VANITY_NUMBERS grew to
    include the 2000-10000 range (see its own comment)."""
    existing = conn.execute("SELECT 1 FROM card_numbers WHERE number = ?", (n,)).fetchone()
    if existing is not None:
        return
    live = conn.execute(
        """
        SELECT 1 FROM user_cards uc WHERE uc.voided = 0 AND
            COALESCE(uc.number_override,
                (SELECT COUNT(*) FROM user_cards uc2 WHERE uc2.obtained_at <= uc.obtained_at)) = ?
        LIMIT 1
        """,
        (n,),
    ).fetchone()
    if live is not None:
        return
    conn.execute(
        "INSERT INTO card_numbers (number, status, updated_at) VALUES (?, 'free', ?)",
        (n, _now()),
    )


def seed_vanity_numbers(conn: sqlite3.Connection) -> None:
    """Proactively surfaces every VANITY_NUMBERS entry and every LOW number (1..
    LOW_NUMBER_SEED_UP_TO) that isn't currently claimed by a live card, so they show up
    in the marketplace as soon as they are free — not only once someone happens to
    burn/evolve a card that held one.

    Skips any candidate above the current "reachable ceiling" outright -- there's no
    point even querying for one that high. That ceiling is normally just the total card
    count (natural numbers only ever span 1..total) -- EXCEPT extract_card_number()
    pins number_override to (count at extraction time) + 1, which can sit one above
    the raw row count right after an extraction and before the next farm catches up to
    it, so the ceiling also has to cover the highest live override actually in play, or
    a just-extracted vanity number could get wrongly re-seeded as 'free' out from under
    the card still showing it. This matters a lot now that VANITY_NUMBERS reaches up to
    10 000: most of that range is still unreachable for a while, and this check is
    cheap (one aggregate query, not one query per candidate)."""
    row = conn.execute(
        "SELECT COUNT(*) AS cnt, COALESCE(MAX(CASE WHEN voided = 0 THEN number_override END), 0) AS max_override "
        "FROM user_cards"
    ).fetchone()
    ceiling = max(row["cnt"], row["max_override"])
    for n in VANITY_NUMBERS:
        if n <= ceiling:
            _seed_number_if_unclaimed(conn, n)
    for n in range(1, LOW_NUMBER_SEED_UP_TO + 1):
        _seed_number_if_unclaimed(conn, n)


def get_numbers_board(limit: int = 200) -> dict:
    """Top `limit` lowest numbers still free/up for auction (what the "Номера" screen
    shows by default — restricted to ALLOWED_AUCTION_NUMBERS, see its comment), plus
    every number currently listed for resale by another player (unrestricted — that's
    a player reselling a number they already legitimately own)."""
    with get_conn() as conn:
        _finalize_expired_number_auctions(conn)
        seed_vanity_numbers(conn)
        allowed = sorted(ALLOWED_AUCTION_NUMBERS)
        placeholders = ",".join("?" for _ in allowed)
        auction_rows = conn.execute(
            "SELECT cn.number, cn.status, cn.highest_bid, cn.highest_bidder_id, cn.bid_expires_at, "
            "u.username AS bidder_username, u.first_name AS bidder_first_name "
            "FROM card_numbers cn LEFT JOIN users u ON u.telegram_id = cn.highest_bidder_id "
            f"WHERE cn.status IN ('free', 'auction') AND cn.number IN ({placeholders}) "
            "ORDER BY cn.number ASC LIMIT ?",
            (*allowed, limit),
        ).fetchall()
        listing_rows = conn.execute(
            "SELECT cn.number, cn.list_price, cn.owner_id, u.username, u.first_name "
            "FROM card_numbers cn JOIN users u ON u.telegram_id = cn.owner_id "
            "WHERE cn.status = 'owned' AND cn.list_price IS NOT NULL ORDER BY cn.number ASC LIMIT ?",
            (limit,),
        ).fetchall()
    return {
        "auctions": [dict(r) for r in auction_rows],
        "listings": [dict(r) for r in listing_rows],
        "min_bid": NUMBER_MIN_BID_GEMS,
    }


def get_all_owned_numbers() -> list[dict]:
    """Every number that currently HAS an owner, for the "Владельцы" tab: both numbers
    formally tracked as 'owned' in card_numbers (won at auction or bought as a resale)
    AND cool/vanity numbers (see ALLOWED_AUCTION_NUMBERS) that nobody has ever
    extracted/bought yet but that are still naturally shown by someone's live card --
    same rule _seed_number_if_unclaimed() uses to decide a number is "in use" and must
    not be seeded as free. Without this second half, a genuinely vanity number like #1
    just silently never appears anywhere in this tab (it's not in Аукцион either, since
    it's in use) even though someone clearly "has" it. Ordered by number ascending,
    same convention as the other number listings."""
    with get_conn() as conn:
        _finalize_expired_number_auctions(conn)
        rows = conn.execute(
            "SELECT cn.number, cn.owner_id, cn.user_card_id, cn.list_price, "
            "u.username, u.first_name "
            "FROM card_numbers cn JOIN users u ON u.telegram_id = cn.owner_id "
            "WHERE cn.status = 'owned' ORDER BY cn.number ASC"
        ).fetchall()
        result = [dict(r) for r in rows]
        tracked_numbers = {r["number"] for r in result}
        # Window function (not a per-row correlated subquery) so this stays cheap no
        # matter how many cards exist -- one sorted pass over user_cards, not O(n^2).
        natural_rows = conn.execute(
            """
            SELECT * FROM (
                SELECT uc.id AS user_card_id, uc.user_id, uc.voided, u.username, u.first_name,
                       COALESCE(uc.number_override, ROW_NUMBER() OVER (ORDER BY uc.obtained_at)) AS number
                FROM user_cards uc JOIN users u ON u.telegram_id = uc.user_id
            ) WHERE voided = 0
            """
        ).fetchall()
        for r in natural_rows:
            n = r["number"]
            if n in tracked_numbers or n not in ALLOWED_AUCTION_NUMBERS:
                continue
            tracked_numbers.add(n)
            result.append({
                "number": n, "owner_id": r["user_id"], "user_card_id": r["user_card_id"],
                "list_price": None, "username": r["username"], "first_name": r["first_name"],
            })
    return sorted(result, key=lambda r: r["number"])


def get_my_numbers(user_id: int) -> list[dict]:
    """Numbers this player currently owns (won auctions or bought resales) — whether
    already attached to one of their cards, or still waiting to be attached/listed."""
    with get_conn() as conn:
        _finalize_expired_number_auctions(conn)
        rows = conn.execute(
            "SELECT number, user_card_id, list_price FROM card_numbers "
            "WHERE owner_id = ? AND status = 'owned' ORDER BY number ASC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def place_number_bid(user_id: int, number: int, amount_gems: int) -> dict:
    """Bids amount_gems on a free/currently-auctioned number. Gems are escrowed right
    away — refunded in full if someone outbids you, spent for good if you win. Every bid
    (including the first one) resets the 24h countdown from that moment. Enforces
    ALLOWED_AUCTION_NUMBERS server-side too — not just a UI filter — so a number that
    was never meant to be for sale can't be bid on via a direct API call either."""
    if number not in ALLOWED_AUCTION_NUMBERS:
        raise NumberNotAvailable()
    with get_conn() as conn:
        _finalize_expired_number_auctions(conn)
        row = conn.execute("SELECT * FROM card_numbers WHERE number = ?", (number,)).fetchone()
        if row is None or row["status"] not in ("free", "auction"):
            raise NumberNotAvailable()
        current_high = row["highest_bid"] or 0
        min_required = max(NUMBER_MIN_BID_GEMS, current_high + 1)
        if amount_gems < min_required:
            raise NumberBidTooLow(min_required)
        gems_row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if gems_row is None or gems_row["gems"] < amount_gems:
            raise InsufficientGems()

        if row["highest_bidder_id"] is not None:
            conn.execute(
                "UPDATE users SET gems = gems + ? WHERE telegram_id = ?",
                (row["highest_bid"], row["highest_bidder_id"]),
            )
        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (amount_gems, user_id))
        window = NUMBER_AUCTION_WINDOW_SECONDS_NEW if row["status"] == "free" else NUMBER_AUCTION_WINDOW_SECONDS
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=window)).isoformat()
        conn.execute(
            "UPDATE card_numbers SET status = 'auction', highest_bid = ?, highest_bidder_id = ?, "
            "bid_expires_at = ?, updated_at = ? WHERE number = ?",
            (amount_gems, user_id, expires_at, _now(), number),
        )
        gems_left = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()["gems"]
    return {"number": number, "bid": amount_gems, "expires_at": expires_at, "gems": gems_left}


def attach_number(user_id: int, number: int, user_card_id: int) -> dict:
    """Pins a number you own onto one of your own cards (moving it off whichever card
    it was on before, if any — that card reverts to its live-computed natural number).
    Whatever number the TARGET card had before (natural or a different bought one) is
    freed back into the pool first."""
    with get_conn() as conn:
        _finalize_expired_number_auctions(conn)
        row = conn.execute("SELECT * FROM card_numbers WHERE number = ?", (number,)).fetchone()
        if row is None or row["owner_id"] != user_id or row["status"] != "owned":
            raise NumberNotAvailable()
        target = _usable_owned_card(conn, user_id, user_card_id)
        if target is None:
            raise NumberCardNotUsable()
        prev_card_id = row["user_card_id"]
        if prev_card_id is not None and prev_card_id != user_card_id:
            conn.execute("UPDATE user_cards SET number_override = NULL WHERE id = ?", (prev_card_id,))
        _free_number(conn, user_card_id)
        conn.execute("UPDATE user_cards SET number_override = ? WHERE id = ?", (number, user_card_id))
        conn.execute(
            "UPDATE card_numbers SET user_card_id = ?, status = 'owned', owner_id = ?, updated_at = ? WHERE number = ?",
            (user_card_id, user_id, _now(), number),
        )
    return {"number": number}


EXTRACT_NUMBER_COST_GEMS = 100


def extract_card_number(user_id: int, user_card_id: int) -> dict:
    """Pulls a card's CURRENT number (natural or already-pinned) off it and straight
    into the caller's own card_numbers inventory (status='owned', unattached) — costs
    EXTRACT_NUMBER_COST_GEMS gems, no auction/bidding needed since it's already their
    own card. Only allowed when that number is rare enough to matter:
    ALLOWED_AUCTION_NUMBERS (repdigits, round hundreds/thousands, 67/69, and every low
    number 1..LOW_NUMBER_SEED_UP_TO — the exact same "special" set the numbers
    marketplace already curates around). The card keeps its identity, but its
    displayed number resets forward to whatever a brand new farm would get right now
    (current total card count + 1) — a number no other card has ever shown, so this
    can never collide with anything already on the board. Once extracted, the number
    shows up in get_my_numbers() exactly like a won auction or bought resale — attach
    it to another card, or list it for sale. Raises InsufficientGems if the balance
    check fails."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, user_id, voided, obtained_at, number_override, listed_price, swap_listed, "
            "staked_at, pvp_round_id, pinned_at, "
            "(SELECT 1 FROM number_giveaways ng WHERE ng.user_card_id = user_cards.id AND ng.drawn_at IS NULL) AS in_giveaway "
            "FROM user_cards WHERE id = ?",
            (user_card_id,),
        ).fetchone()
        if row is None or row["user_id"] != user_id or row["voided"]:
            raise NumberCardNotUsable()
        if (row["listed_price"] is not None or row["swap_listed"] or row["staked_at"] is not None
                or row["pvp_round_id"] is not None or row["pinned_at"] is not None or row["in_giveaway"]):
            raise NumberCardNotUsable()

        if row["number_override"] is not None:
            current_number = row["number_override"]
        else:
            current_number = conn.execute(
                "SELECT COUNT(*) FROM user_cards WHERE obtained_at <= ?", (row["obtained_at"],)
            ).fetchone()[0]
        if current_number not in ALLOWED_AUCTION_NUMBERS:
            raise NumberNotRare()

        gems_row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if gems_row is None or gems_row["gems"] < EXTRACT_NUMBER_COST_GEMS:
            raise InsufficientGems()
        conn.execute(
            "UPDATE users SET gems = gems - ? WHERE telegram_id = ?",
            (EXTRACT_NUMBER_COST_GEMS, user_id),
        )

        now = _now()
        # Hand the number straight to its own former holder as 'owned' — same upsert
        # shape as _free_number(), just landing on 'owned'+owner_id instead of 'free'.
        conn.execute(
            """
            INSERT INTO card_numbers (number, status, owner_id, user_card_id, updated_at)
            VALUES (?, 'owned', ?, NULL, ?)
            ON CONFLICT(number) DO UPDATE SET
                status = 'owned', owner_id = excluded.owner_id, user_card_id = NULL,
                highest_bid = NULL, highest_bidder_id = NULL, bid_expires_at = NULL,
                list_price = NULL, updated_at = excluded.updated_at
            """,
            (current_number, user_id, now),
        )
        # Next number ever to be assigned by a real farm right now — never held by any
        # existing card, so pinning it here can't collide with anything on the board.
        next_number = conn.execute("SELECT COUNT(*) FROM user_cards").fetchone()[0] + 1
        conn.execute("UPDATE user_cards SET number_override = ? WHERE id = ?", (next_number, user_card_id))
        gems_left = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()["gems"]
    return {"extracted_number": current_number, "new_number": next_number, "gems": gems_left}


def list_number_for_sale(user_id: int, number: int, price_gems: int) -> None:
    """Puts a number you own up for sale to another player -- only a FREE number
    (not currently pinned to any of your cards) can be listed; unpin it first
    (attach a different number to that card, which bumps this one back into your own
    bank -- see _free_number()) before it's sellable."""
    if price_gems <= 0:
        raise ListingPriceTooLow(1)
    with get_conn() as conn:
        _finalize_expired_number_auctions(conn)
        row = conn.execute("SELECT * FROM card_numbers WHERE number = ?", (number,)).fetchone()
        if row is None or row["owner_id"] != user_id or row["status"] != "owned":
            raise NumberNotAvailable()
        if row["user_card_id"] is not None:
            raise NumberNotAvailable()
        conn.execute(
            "UPDATE card_numbers SET list_price = ?, updated_at = ? WHERE number = ?",
            (price_gems, _now(), number),
        )


def cancel_number_listing(user_id: int, number: int) -> None:
    with get_conn() as conn:
        row = conn.execute("SELECT owner_id FROM card_numbers WHERE number = ?", (number,)).fetchone()
        if row is None or row["owner_id"] != user_id:
            raise NumberCardNotUsable()
        conn.execute("UPDATE card_numbers SET list_price = NULL, updated_at = ? WHERE number = ?", (_now(), number))


def buy_listed_number(buyer_id: int, number: int, buyer_user_card_id: int) -> dict:
    """Buys a number another player listed for resale, paying them directly (no house
    cut) and pinning it straight onto one of the buyer's own cards. If the number was
    still pinned to the seller's card, that card reverts to its live-computed natural
    number instead of being left duplicated."""
    with get_conn() as conn:
        _finalize_expired_number_auctions(conn)
        row = conn.execute("SELECT * FROM card_numbers WHERE number = ?", (number,)).fetchone()
        if row is None or row["list_price"] is None:
            raise NumberNotAvailable()
        if row["owner_id"] == buyer_id:
            raise NumberCardNotUsable()
        buyer_card = _usable_owned_card(conn, buyer_id, buyer_user_card_id)
        if buyer_card is None:
            raise NumberCardNotUsable()
        price = row["list_price"]
        gems_row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (buyer_id,)).fetchone()
        if gems_row is None or gems_row["gems"] < price:
            raise InsufficientGems()

        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (price, buyer_id))
        conn.execute(
            "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? WHERE telegram_id = ?",
            (price, price, row["owner_id"]),
        )
        if row["user_card_id"] is not None:
            conn.execute("UPDATE user_cards SET number_override = NULL WHERE id = ?", (row["user_card_id"],))
        _free_number(conn, buyer_user_card_id)
        conn.execute("UPDATE user_cards SET number_override = ? WHERE id = ?", (number, buyer_user_card_id))
        conn.execute(
            "UPDATE card_numbers SET owner_id = ?, user_card_id = ?, list_price = NULL, status = 'owned', updated_at = ? "
            "WHERE number = ?",
            (buyer_id, buyer_user_card_id, _now(), number),
        )
    return {"number": number, "price": price}





# ---------------------------------------------------------------------------
# Custom-name marketplace ("Имена") + Custom NFT ("Obsidian") creation.
# Names mirror the card_numbers auction machinery (see its schema comment) with one
# structural difference: numbers are pre-seeded from a curated pool and start 'free',
# while a name only starts existing the moment someone creates it -- creation IS the
# first bid, there is no 'free' status for names.
# ---------------------------------------------------------------------------

NAME_MIN_BID_GEMS = 25
NAME_AUCTION_WINDOW_SECONDS = 12 * 60 * 60  # same 12h window as numbers, reset on every bid
CUSTOM_NAME_MAX_LEN = 16
CUSTOM_NAME_MIN_LEN = 3
CUSTOM_NFT_CREATE_COST_GEMS = 500  # charged the first time a card is turned into Obsidian
CUSTOM_NFT_EDIT_COST_GEMS = 100     # charged to swap the name/number on an already-Obsidian card


class NameInvalid(Exception):
    """Raised when a proposed name fails validation -- empty, too long, or contains
    anything other than English letters/digits."""


class NameTaken(Exception):
    """Raised by create_name_auction() when the name (case-insensitive) already exists
    in card_names -- auctioned, owned, or otherwise."""


class NameNotAvailable(Exception):
    """Raised when a name isn't in the state the caller expects -- already someone
    else's, not mid-auction, not listed for resale, etc."""


class NameBidTooLow(Exception):
    """Raised by place_name_bid() when amount_gems doesn't beat the current highest
    bid (or NAME_MIN_BID_GEMS, whichever is higher) -- carries the minimum that would
    have worked."""
    def __init__(self, min_bid: int):
        self.min_bid = min_bid
        super().__init__(f"minimum bid is {min_bid} gems")


def validate_custom_name(name: str) -> str:
    """Trims, checks length (CUSTOM_NAME_MIN_LEN..CUSTOM_NAME_MAX_LEN) and charset
    (English letters/digits only), and returns the canonical lowercase form used as the
    card_names primary key. Raises NameInvalid on any failure."""
    trimmed = (name or "").strip()
    if len(trimmed) < CUSTOM_NAME_MIN_LEN or len(trimmed) > CUSTOM_NAME_MAX_LEN:
        raise NameInvalid()
    if not all(("a" <= ch.lower() <= "z") or ch.isdigit() for ch in trimmed):
        raise NameInvalid()
    return trimmed.lower()


def _finalize_expired_name_auctions(conn: sqlite3.Connection) -> None:
    """Any name auction whose 12h window has lapsed with no new bid flips to 'owned' --
    the last bidder wins, gems already paid stay spent. Same lazy-expiry, called
    opportunistically at the top of every names-marketplace read/write, philosophy as
    _finalize_expired_number_auctions()."""
    now = _now()
    rows = conn.execute(
        "SELECT name, highest_bidder_id FROM card_names WHERE status = 'auction' AND bid_expires_at <= ?",
        (now,),
    ).fetchall()
    for r in rows:
        conn.execute(
            "UPDATE card_names SET status = 'owned', owner_id = ?, "
            "highest_bid = NULL, highest_bidder_id = NULL, bid_expires_at = NULL, updated_at = ? "
            "WHERE name = ?",
            (r["highest_bidder_id"], now, r["name"]),
        )


# The admin's own real @handle should never leak in anything a player can see (PvP
# "История" log, the round-conclusion reveal, the public-chat PvP announcement) --
# shown as a generic "Бот" label there instead. Mirrors bot.py's own _mask_username,
# duplicated here (not imported) since bot.py imports database.py, not the other way
# around.
ADMIN_USERNAME_MASK = "rzabeyda"


def _mask_username(username: str | None) -> str | None:
    if username and username.lower() == ADMIN_USERNAME_MASK:
        return "Бот"
    return username


# Permanently blocked from ever being created as a custom card name (the admin's own
# real handle and its variants) -- checked in create_name_auction() below, which is the
# only place a name starts existing, so this is the single choke point for the block.
RESERVED_NAMES = {"rzabeyda", "zabeyda", "zzabeyda"}


def create_name_auction(user_id: int, name: str) -> dict:
    """Registers a brand-new name and opens its NAME_AUCTION_WINDOW_SECONDS-long auction
    with the creator's own NAME_MIN_BID_GEMS bid already placed -- unlike numbers, a name
    only starts existing the moment someone creates it, so creation IS the first bid,
    gems escrowed immediately (refunded in full if someone outbids the creator later)."""
    canonical = validate_custom_name(name)
    if canonical in RESERVED_NAMES:
        raise NameTaken()
    with get_conn() as conn:
        _finalize_expired_name_auctions(conn)
        existing = conn.execute("SELECT 1 FROM card_names WHERE name = ?", (canonical,)).fetchone()
        if existing is not None:
            raise NameTaken()
        gems_row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if gems_row is None or gems_row["gems"] < NAME_MIN_BID_GEMS:
            raise InsufficientGems()
        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (NAME_MIN_BID_GEMS, user_id))
        now = _now()
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=NAME_AUCTION_WINDOW_SECONDS)).isoformat()
        conn.execute(
            "INSERT INTO card_names (name, status, highest_bid, highest_bidder_id, bid_expires_at, created_at, updated_at) "
            "VALUES (?, 'auction', ?, ?, ?, ?, ?)",
            (canonical, NAME_MIN_BID_GEMS, user_id, expires_at, now, now),
        )
        gems_left = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()["gems"]
    return {"name": canonical, "bid": NAME_MIN_BID_GEMS, "expires_at": expires_at, "gems": gems_left}


def place_name_bid(user_id: int, name: str, amount_gems: int) -> dict:
    """Outbids the current holder of a name still mid-auction -- same escrow/refund/
    timer-reset shape as place_number_bid()."""
    canonical = (name or "").strip().lower()
    with get_conn() as conn:
        _finalize_expired_name_auctions(conn)
        row = conn.execute("SELECT * FROM card_names WHERE name = ?", (canonical,)).fetchone()
        if row is None or row["status"] != "auction":
            raise NameNotAvailable()
        current_high = row["highest_bid"] or 0
        min_required = max(NAME_MIN_BID_GEMS, current_high + 1)
        if amount_gems < min_required:
            raise NameBidTooLow(min_required)
        gems_row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if gems_row is None or gems_row["gems"] < amount_gems:
            raise InsufficientGems()
        if row["highest_bidder_id"] is not None:
            conn.execute(
                "UPDATE users SET gems = gems + ? WHERE telegram_id = ?",
                (row["highest_bid"], row["highest_bidder_id"]),
            )
        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (amount_gems, user_id))
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=NAME_AUCTION_WINDOW_SECONDS)).isoformat()
        conn.execute(
            "UPDATE card_names SET highest_bid = ?, highest_bidder_id = ?, bid_expires_at = ?, updated_at = ? WHERE name = ?",
            (amount_gems, user_id, expires_at, _now(), canonical),
        )
        gems_left = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()["gems"]
    return {"name": canonical, "bid": amount_gems, "expires_at": expires_at, "gems": gems_left}


def get_names_board(limit: int = 200) -> dict:
    """Names currently mid-auction (biddable) plus names already owned and listed for
    resale -- mirrors get_numbers_board()'s two-part shape, minus the curated pool
    (names have none -- every name starts life as someone's own bid)."""
    with get_conn() as conn:
        _finalize_expired_name_auctions(conn)
        auction_rows = conn.execute(
            "SELECT name, highest_bid, highest_bidder_id, bid_expires_at FROM card_names "
            "WHERE status = 'auction' ORDER BY bid_expires_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        resale_rows = conn.execute(
            "SELECT cn.name, cn.list_price, cn.owner_id, u.username, u.first_name "
            "FROM card_names cn JOIN users u ON u.telegram_id = cn.owner_id "
            "WHERE cn.status = 'owned' AND cn.list_price IS NOT NULL ORDER BY cn.updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {"auctions": [dict(r) for r in auction_rows], "listings": [dict(r) for r in resale_rows], "min_bid": NAME_MIN_BID_GEMS}


def get_all_owned_names() -> list[dict]:
    """Every name that currently has an owner, for the "Владельцы" tab -- mirrors
    get_all_owned_numbers(), but simpler: a name only ever exists once someone creates
    it (no natural/vanity pool to also scan for "in use but untracked" entries), so
    this is just every card_names row already sitting at status='owned'."""
    with get_conn() as conn:
        _finalize_expired_name_auctions(conn)
        rows = conn.execute(
            "SELECT cn.name, cn.owner_id, cn.user_card_id, cn.list_price, "
            "u.username, u.first_name "
            "FROM card_names cn JOIN users u ON u.telegram_id = cn.owner_id "
            "WHERE cn.status = 'owned' ORDER BY cn.name ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_my_names(user_id: int) -> list[dict]:
    """Names this player currently owns (won auctions or bought resales) -- whether
    already attached to one of their Custom NFT cards, or still sitting unused in the
    bank."""
    with get_conn() as conn:
        _finalize_expired_name_auctions(conn)
        rows = conn.execute(
            "SELECT name, user_card_id, list_price FROM card_names "
            "WHERE owner_id = ? AND status = 'owned' ORDER BY name ASC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_name_for_sale(user_id: int, name: str, price_gems: int) -> None:
    """Puts a name you own up for sale to another player -- keeps working/showing on
    your Custom NFT card until it actually sells, same as list_number_for_sale()."""
    if price_gems <= 0:
        raise ListingPriceTooLow(1)
    canonical = (name or "").strip().lower()
    with get_conn() as conn:
        _finalize_expired_name_auctions(conn)
        row = conn.execute("SELECT * FROM card_names WHERE name = ?", (canonical,)).fetchone()
        if row is None or row["owner_id"] != user_id or row["status"] != "owned":
            raise NameNotAvailable()
        conn.execute(
            "UPDATE card_names SET list_price = ?, updated_at = ? WHERE name = ?",
            (price_gems, _now(), canonical),
        )


def cancel_name_listing(user_id: int, name: str) -> None:
    canonical = (name or "").strip().lower()
    with get_conn() as conn:
        row = conn.execute("SELECT owner_id FROM card_names WHERE name = ?", (canonical,)).fetchone()
        if row is None or row["owner_id"] != user_id:
            raise NameNotAvailable()
        conn.execute("UPDATE card_names SET list_price = NULL, updated_at = ? WHERE name = ?", (_now(), canonical))


def buy_listed_name(buyer_id: int, name: str) -> dict:
    """Buys a name another player listed for resale, paying them directly (no house
    cut). If it was still attached to the seller's Custom NFT card, that card's
    custom_name is cleared (its custom_rarity/Obsidian status is NOT -- once granted
    that's permanent, only voiding clears it) and the name lands unattached in the
    buyer's own bank, ready to be used via create_custom_nft()."""
    canonical = (name or "").strip().lower()
    with get_conn() as conn:
        _finalize_expired_name_auctions(conn)
        row = conn.execute("SELECT * FROM card_names WHERE name = ?", (canonical,)).fetchone()
        if row is None or row["list_price"] is None:
            raise NameNotAvailable()
        if row["owner_id"] == buyer_id:
            raise NameNotAvailable()
        price = row["list_price"]
        gems_row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (buyer_id,)).fetchone()
        if gems_row is None or gems_row["gems"] < price:
            raise InsufficientGems()
        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (price, buyer_id))
        conn.execute(
            "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? WHERE telegram_id = ?",
            (price, price, row["owner_id"]),
        )
        if row["user_card_id"] is not None:
            conn.execute("UPDATE user_cards SET custom_name = NULL WHERE id = ?", (row["user_card_id"],))
        conn.execute(
            "UPDATE card_names SET owner_id = ?, user_card_id = NULL, list_price = NULL, status = 'owned', updated_at = ? "
            "WHERE name = ?",
            (buyer_id, _now(), canonical),
        )
    return {"name": canonical, "price": price}


def create_custom_nft(user_id: int, user_card_id: int, name: str, number: int) -> dict:
    """Turns one of the caller's own cards into a permanent Obsidian custom NFT, OR --
    if the target card is already Obsidian -- swaps its name/number for a different one
    from the caller's own bank. Charges CUSTOM_NFT_CREATE_COST_GEMS the first time, or
    the cheaper CUSTOM_NFT_EDIT_COST_GEMS on an already-Obsidian card, deducted from
    the caller's gems (an actual charge, not just a balance-on-hand threshold). Also
    requires a name the caller already owns in their name-bank and a number the caller
    already owns in their number-bank (both via the marketplaces above). Sets
    custom_name + custom_rarity='diamond' on the target card -- permanent, only
    cleared if the row is later voided (see the voided=1 UPDATE statements) -- and
    pins the number using the same internal steps as attach_number(), reusing the
    already-open connection instead of calling that public function directly (this
    codebase's convention: public entry points open their own connection, so they're
    never nested inside one another)."""
    canonical = (name or "").strip().lower()
    with get_conn() as conn:
        _finalize_expired_name_auctions(conn)
        _finalize_expired_number_auctions(conn)

        target = _usable_owned_card(conn, user_id, user_card_id)
        if target is None:
            raise NumberCardNotUsable()

        existing_custom_name = conn.execute(
            "SELECT custom_name FROM user_cards WHERE id = ?", (user_card_id,)
        ).fetchone()["custom_name"]
        is_edit = bool(existing_custom_name)
        cost = CUSTOM_NFT_EDIT_COST_GEMS if is_edit else CUSTOM_NFT_CREATE_COST_GEMS

        gems_row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if gems_row is None or gems_row["gems"] < cost:
            raise InsufficientGems()

        name_row = conn.execute("SELECT * FROM card_names WHERE name = ?", (canonical,)).fetchone()
        if name_row is None or name_row["owner_id"] != user_id or name_row["status"] != "owned":
            raise NameNotAvailable()

        number_row = conn.execute("SELECT * FROM card_numbers WHERE number = ?", (number,)).fetchone()
        if number_row is None or number_row["owner_id"] != user_id or number_row["status"] != "owned":
            raise NumberNotAvailable()

        # A name/number can only ever show on one card at a time -- detach from
        # wherever it sat before (if anywhere else).
        if name_row["user_card_id"] is not None and name_row["user_card_id"] != user_card_id:
            conn.execute("UPDATE user_cards SET custom_name = NULL WHERE id = ?", (name_row["user_card_id"],))

        # Re-running this on an already-Obsidian card (swapping its name for a
        # different one from the bank) must also free up whatever name it was WEARING
        # before, or that old name's card_names row keeps pointing at this card forever
        # (permanently stuck "in use", even though the card no longer shows it).
        prev_name_row = conn.execute("SELECT custom_name FROM user_cards WHERE id = ?", (user_card_id,)).fetchone()
        if prev_name_row and prev_name_row["custom_name"] and prev_name_row["custom_name"] != canonical:
            conn.execute(
                "UPDATE card_names SET user_card_id = NULL, updated_at = ? WHERE name = ? AND user_card_id = ?",
                (_now(), prev_name_row["custom_name"], user_card_id),
            )

        conn.execute(
            "UPDATE card_names SET user_card_id = ?, updated_at = ? WHERE name = ?",
            (user_card_id, _now(), canonical),
        )

        prev_number_card_id = number_row["user_card_id"]
        if prev_number_card_id is not None and prev_number_card_id != user_card_id:
            conn.execute("UPDATE user_cards SET number_override = NULL WHERE id = ?", (prev_number_card_id,))
        _free_number(conn, user_card_id)  # free whatever number the target card currently shows
        conn.execute("UPDATE user_cards SET number_override = ? WHERE id = ?", (number, user_card_id))
        conn.execute(
            "UPDATE card_numbers SET user_card_id = ?, status = 'owned', owner_id = ?, updated_at = ? WHERE number = ?",
            (user_card_id, user_id, _now(), number),
        )

        conn.execute(
            "UPDATE user_cards SET custom_name = ?, custom_rarity = 'diamond' WHERE id = ?",
            (canonical, user_card_id),
        )
        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (cost, user_id))
        new_gems = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (user_id,)).fetchone()["gems"]
    return {"user_card_id": user_card_id, "custom_name": canonical, "number": number, "cost": cost, "is_edit": is_edit, "gems": new_gems}


def get_all_obsidian_cards() -> list[dict]:
    """Every card, across ALL players, that has been turned into an Obsidian card via
    create_custom_nft() (custom_name set) -- backs the "OBSIDIAN" browse button under
    the rarity summary in Модели, showing the full catalog of custom names/numbers the
    same way the profile grid shows a player's own cards. custom_rarity is always
    'diamond' for these (see create_custom_nft above), so it's hardcoded here rather
    than re-selected from the row."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT uc.id AS user_card_id, c.filename, uc.custom_name AS name, 'diamond' AS rarity,
                   COALESCE(uc.number_override,
                       (SELECT COUNT(*) FROM user_cards uc2 WHERE uc2.obtained_at <= uc.obtained_at)) AS drop_number
            FROM user_cards uc
            JOIN cards c ON c.id = uc.card_id
            WHERE uc.custom_name IS NOT NULL AND uc.voided = 0
            ORDER BY uc.obtained_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- Wall (Стена) — a personal curated showcase in Profile ----------
# A player can pin up to MAX_WALL_CARDS of their own cards (pinned_at set) to admire
# separately from the full collection grid. Purely cosmetic — pinning doesn't lock the
# card (it can still be listed/staked/swapped/sent to PvP while pinned). Unpinned
# automatically wherever a card's ownership changes or it's voided/reused, so the Wall
# can never show a card that's no longer the player's, or that isn't the one they chose.
MAX_WALL_CARDS = 9


class WallCardNotUsable(Exception):
    ...


class WallFull(Exception):
    def __init__(self, max_cards: int):
        self.max_cards = max_cards
        super().__init__(f"wall is full (max {max_cards})")


def pin_to_wall(user_id: int, user_card_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, pinned_at FROM user_cards WHERE id = ? AND user_id = ? AND voided = 0",
            (user_card_id, user_id),
        ).fetchone()
        if row is None:
            raise WallCardNotUsable()
        if row["pinned_at"] is not None:
            return {"pinned": True}
        count = conn.execute(
            "SELECT COUNT(*) FROM user_cards WHERE user_id = ? AND voided = 0 AND pinned_at IS NOT NULL",
            (user_id,),
        ).fetchone()[0]
        if count >= MAX_WALL_CARDS:
            raise WallFull(MAX_WALL_CARDS)
        conn.execute("UPDATE user_cards SET pinned_at = ? WHERE id = ?", (_now(), user_card_id))
    return {"pinned": True}


def unpin_from_wall(user_id: int, user_card_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE user_cards SET pinned_at = NULL WHERE id = ? AND user_id = ?",
            (user_card_id, user_id),
        )


def get_wall(user_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT uc.id AS user_card_id, c.filename, c.name, c.rarity, uc.pinned_at,
                   COALESCE(uc.number_override,
                       (SELECT COUNT(*) FROM user_cards uc2 WHERE uc2.obtained_at <= uc.obtained_at)) AS drop_number
            FROM user_cards uc
            JOIN cards c ON c.id = uc.card_id
            WHERE uc.user_id = ? AND uc.voided = 0 AND uc.pinned_at IS NOT NULL
            ORDER BY uc.pinned_at ASC
            """,
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_inventory(user_id: int) -> list[dict]:
    """Cards the user owns — one row per copy, shown separately even when duplicated
    (duplicates are common since supply is unlimited). drop_number is a GLOBAL rank —
    position among every card ever farmed/crafted by any user, ordered by obtained_at —
    not a per-user counter."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT uc.id AS user_card_id, c.id AS card_id, c.filename,
                   COALESCE(uc.custom_name, c.name) AS name,
                   COALESCE(uc.custom_rarity, c.rarity) AS rarity,
                   uc.custom_name AS custom_name,
                   uc.listed_price, uc.swap_listed, uc.staked_at, uc.pvp_round_id, uc.pinned_at,
                   COALESCE(uc.number_override,
                       (SELECT COUNT(*) FROM user_cards uc2 WHERE uc2.obtained_at <= uc.obtained_at)) AS drop_number,
                   (SELECT 1 FROM number_giveaways ng WHERE ng.user_card_id = uc.id AND ng.drawn_at IS NULL) AS in_giveaway
            FROM user_cards uc
            JOIN cards c ON c.id = uc.card_id
            WHERE uc.user_id = ? AND uc.voided = 0
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
        _detach_number_on_card_transfer(conn, user_card_id)
        conn.execute(
            "UPDATE user_cards SET user_id = ?, transfer_pending = 0, number_override = NULL, pinned_at = NULL WHERE id = ?",
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


def get_last_gem_drop_time_by_amount(amount: int) -> str | None:
    """Same as get_last_gem_drop_time() but scoped to one drop size — lets the daily
    100-gem airdrop track its own cadence independently of the regular 25-gem drops
    that interleave with it in the same table."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT created_at FROM gem_drops WHERE amount = ? ORDER BY id DESC LIMIT 1",
            (amount,),
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
# Number giveaways — admin picks ONE of their own cards by its display number (the
# same number a player sees next to a card in the app) and raffles it off live in
# PUBLIC_CHAT: a "Участвовать" button, anyone who taps it is entered, and after
# duration_hours one random entrant wins that exact card. Deliberately NOT
# reserved/locked server-side for the whole window the way listing/staking/PvP/Wall
# are — this is admin-only and a short window, so the admin is simply expected not to
# also sell/craft/burn/PvP/re-pin that exact card while its giveaway is pending.
# draw_number_giveaway() re-checks at draw time and skips the transfer (reporting why)
# if that assumption was broken.
# ---------------------------------------------------------------------------

class NumberGiveawayError(Exception):
    """Raised by create_number_giveaway() when the admin doesn't currently own a
    live, free (unbusy) card showing the given display number."""


def _find_admin_number_card(conn: sqlite3.Connection, admin_id: int, number: int) -> sqlite3.Row | None:
    rows = conn.execute(
        """
        SELECT uc.id AS user_card_id, c.filename, c.name, c.rarity,
               uc.listed_price, uc.swap_listed, uc.staked_at, uc.pvp_round_id, uc.pinned_at,
               COALESCE(uc.number_override,
                   (SELECT COUNT(*) FROM user_cards uc2 WHERE uc2.obtained_at <= uc.obtained_at)) AS drop_number
        FROM user_cards uc
        JOIN cards c ON c.id = uc.card_id
        WHERE uc.user_id = ? AND uc.voided = 0
        """,
        (admin_id,),
    ).fetchall()
    return next((r for r in rows if r["drop_number"] == number), None)


def create_number_giveaway(admin_id: int, number: int, duration_hours: float = 1.0) -> dict:
    """Admin's /numbergiveaway command: validates the admin owns a free (not
    listed/swapped/staked/in PvP/pinned to the Wall) card currently showing `number`,
    creates a number_giveaways row that auto-draws after duration_hours, and returns
    {"id", "card": {"user_card_id","name","rarity","filename","number"}}."""
    with get_conn() as conn:
        match = _find_admin_number_card(conn, admin_id, number)
        if match is None:
            raise NumberGiveawayError(f"у тебя нет карты с номером {number}")
        if (match["listed_price"] is not None or match["swap_listed"] or match["staked_at"] is not None
                or match["pvp_round_id"] is not None or match["pinned_at"] is not None):
            raise NumberGiveawayError(f"карта №{number} сейчас занята (продажа/обмен/стейк/пвп/стена) — сними сначала")
        draw_at = (datetime.now(timezone.utc) + timedelta(hours=duration_hours)).isoformat()
        cur = conn.execute(
            "INSERT INTO number_giveaways (admin_id, user_card_id, number, card_name, card_rarity, "
            "card_filename, created_at, draw_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (admin_id, match["user_card_id"], number, match["name"], match["rarity"], match["filename"], _now(), draw_at),
        )
        return {
            "id": cur.lastrowid,
            "card": {
                "user_card_id": match["user_card_id"], "name": match["name"],
                "rarity": match["rarity"], "filename": match["filename"], "number": number,
            },
        }


def set_number_giveaway_message(giveaway_id: int, message_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE number_giveaways SET message_id = ? WHERE id = ?", (message_id, giveaway_id))


def join_number_giveaway(giveaway_id: int, user_id: int) -> str:
    """Same return convention as join_giveaway(): 'joined', 'already_joined', 'drawn',
    'not_found', plus 'is_admin' (the admin can't win their own card)."""
    with get_conn() as conn:
        giveaway = conn.execute(
            "SELECT admin_id, drawn_at FROM number_giveaways WHERE id = ?", (giveaway_id,)
        ).fetchone()
        if giveaway is None:
            return "not_found"
        if giveaway["admin_id"] == user_id:
            return "is_admin"
        if giveaway["drawn_at"] is not None:
            return "drawn"
        existing = conn.execute(
            "SELECT 1 FROM number_giveaway_entries WHERE number_giveaway_id = ? AND user_id = ?",
            (giveaway_id, user_id),
        ).fetchone()
        if existing:
            return "already_joined"
        conn.execute(
            "INSERT INTO number_giveaway_entries (number_giveaway_id, user_id, joined_at) VALUES (?, ?, ?)",
            (giveaway_id, user_id, _now()),
        )
        return "joined"


def get_number_giveaway(giveaway_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM number_giveaways WHERE id = ?", (giveaway_id,)).fetchone()
        return dict(row) if row else None


def count_number_giveaway_entries(giveaway_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM number_giveaway_entries WHERE number_giveaway_id = ?", (giveaway_id,)
        ).fetchone()[0]


# Reminder thresholds for chat card giveaways (/numbergiveaway and /cardsgiveaway) --
# a "1 hour left" ping and a "5 minutes left" ping into PUBLIC_CHAT before the draw,
# each fired exactly once per giveaway (see reminder_1h_sent/reminder_5m_sent columns
# + giveaway_reminder_scheduler() in bot.py, which polls every 60s -- frequent enough
# that the 5-minute threshold doesn't get skipped over between checks).
GIVEAWAY_REMINDER_1H_SECONDS = 3600
GIVEAWAY_REMINDER_5M_SECONDS = 300


def _giveaways_needing_reminder(table: str, kind: str, extra_where: str = "") -> list[dict]:
    """Shared by number_giveaways and card_batch_giveaways (same columns: created_at,
    draw_at, drawn_at, reminder_1h_sent, reminder_5m_sent). Only fires a threshold
    that's actually shorter than the giveaway's own total duration -- a 30-minute
    giveaway never gets a redundant "1 hour left" ping the instant it's created."""
    threshold = GIVEAWAY_REMINDER_1H_SECONDS if kind == "1h" else GIVEAWAY_REMINDER_5M_SECONDS
    flag_col = "reminder_1h_sent" if kind == "1h" else "reminder_5m_sent"
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE drawn_at IS NULL AND {flag_col} = 0" + extra_where
        ).fetchall()
    now = datetime.now(timezone.utc)
    due = []
    for row in rows:
        draw_at = datetime.fromisoformat(row["draw_at"])
        created_at = datetime.fromisoformat(row["created_at"])
        remaining = (draw_at - now).total_seconds()
        total_duration = (draw_at - created_at).total_seconds()
        if 0 < remaining <= threshold and total_duration > threshold:
            due.append(dict(row))
    return due


def get_number_giveaways_needing_reminder(kind: str) -> list[dict]:
    """kind is '1h' or '5m'. Standalone /numbergiveaway rows only (batch_id IS NULL --
    same convention as get_due_number_giveaways(): batch cards are reminded via
    get_card_batch_giveaways_needing_reminder() instead, once for the whole batch)."""
    return _giveaways_needing_reminder("number_giveaways", kind, " AND batch_id IS NULL")


def mark_number_giveaway_reminder_sent(giveaway_id: int, kind: str) -> None:
    flag_col = "reminder_1h_sent" if kind == "1h" else "reminder_5m_sent"
    with get_conn() as conn:
        conn.execute(f"UPDATE number_giveaways SET {flag_col} = 1 WHERE id = ?", (giveaway_id,))


def get_card_batch_giveaways_needing_reminder(kind: str) -> list[dict]:
    """kind is '1h' or '5m'."""
    return _giveaways_needing_reminder("card_batch_giveaways", kind)


def mark_card_batch_giveaway_reminder_sent(batch_id: int, kind: str) -> None:
    flag_col = "reminder_1h_sent" if kind == "1h" else "reminder_5m_sent"
    with get_conn() as conn:
        conn.execute(f"UPDATE card_batch_giveaways SET {flag_col} = 1 WHERE id = ?", (batch_id,))


def get_due_number_giveaways() -> list[dict]:
    """Standalone /numbergiveaway rows ONLY -- batch_id IS NULL excludes every card
    that belongs to a /cardsgiveaway batch (those share this same table, tagged with
    batch_id, but must be drawn exclusively by draw_card_batch_giveaway() against the
    batch's own shared entries pool; without this filter this function was stealing
    and 'resolving' each batch card here first, against number_giveaway_entries (which
    is always empty for a batch card -- its real participants are in
    card_batch_giveaway_entries instead), reporting a false 'no participants' for
    every single card and leaving nothing for the batch draw to actually distribute)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM number_giveaways WHERE drawn_at IS NULL AND draw_at <= ? AND batch_id IS NULL", (_now(),)
        ).fetchall()
        return [dict(row) for row in rows]


def draw_number_giveaway(giveaway_id: int) -> dict:
    """Randomly picks one entrant (if any) and transfers the card to them — same
    ownership-reassignment pattern as create_card_giveaway(). Returns {"card",
    "winner" (or None), "total_entries", "transferred" (bool), "reason" (set only when
    a winner was picked but the transfer couldn't happen — e.g. the admin sold/used
    the card meanwhile)}."""
    with get_conn() as conn:
        giveaway = conn.execute("SELECT * FROM number_giveaways WHERE id = ?", (giveaway_id,)).fetchone()
        entries = [
            dict(row) for row in conn.execute(
                "SELECT u.telegram_id, u.username, u.first_name FROM number_giveaway_entries ge "
                "JOIN users u ON u.telegram_id = ge.user_id WHERE ge.number_giveaway_id = ?",
                (giveaway_id,),
            ).fetchall()
        ]
        card = {
            "number": giveaway["number"], "name": giveaway["card_name"],
            "rarity": giveaway["card_rarity"], "filename": giveaway["card_filename"],
        }
        winner = random.choice(entries) if entries else None
        transferred = False
        reason = None
        if winner is not None:
            row = conn.execute(
                "SELECT user_id, voided, listed_price, swap_listed, staked_at, pvp_round_id, pinned_at "
                "FROM user_cards WHERE id = ?",
                (giveaway["user_card_id"],),
            ).fetchone()
            if row is None or row["voided"] or row["user_id"] != giveaway["admin_id"]:
                reason = "карта уже недоступна (продана/потрачена)"
            elif (row["listed_price"] is not None or row["swap_listed"] or row["staked_at"] is not None
                    or row["pvp_round_id"] is not None or row["pinned_at"] is not None):
                reason = "карта сейчас занята (продажа/обмен/стейк/пвп/стена)"
            else:
                _detach_number_on_card_transfer(conn, giveaway["user_card_id"])
                conn.execute(
                    "UPDATE user_cards SET user_id = ?, listed_price = NULL, swap_listed = 0, "
                    "staked_at = NULL, pvp_round_id = NULL, number_override = NULL, pinned_at = NULL WHERE id = ?",
                    (winner["telegram_id"], giveaway["user_card_id"]),
                )
                transferred = True
        conn.execute("UPDATE number_giveaways SET drawn_at = ? WHERE id = ?", (_now(), giveaway_id))
        return {
            "card": card, "winner": winner, "total_entries": len(entries),
            "transferred": transferred, "reason": reason,
        }


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Batch card giveaways -- /cardsgiveaway [часов]: admin gives away ALL of their
# currently-owned non-diamond cards at once, live in chat for admin-chosen duration.
# Same join-button-then-auto-draw shape as number_giveaways, just with many cards and
# ONE shared entries pool instead of one card + its own pool. See schema comment
# above card_batch_giveaways for why each card still gets its own number_giveaways
# row (free reuse of every existing "is this card busy" check in the codebase).
# ---------------------------------------------------------------------------

class CardBatchGiveawayError(Exception):
    """Raised by create_card_batch_giveaway() -- no eligible (non-diamond, not
    already busy) cards to give away."""


def create_card_batch_giveaway(admin_id: int, duration_hours: float) -> dict:
    """Snapshots every non-diamond card the admin owns right now that isn\'t already
    busy (listed/swapped/staked/PvP/pinned/in another giveaway), opens one
    card_batch_giveaways row plus one number_giveaways row per card (batch_id links
    them), and returns {"batch_id", "draw_at", "cards": [{...}]}. Raises
    CardBatchGiveawayError if there\'s nothing eligible. Cards are locked (via the
    shared number_giveaways \'busy\' check) from the instant this returns, same as a
    single /numbergiveaway."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT uc.id AS user_card_id, c.name, c.rarity, c.filename,
                   COALESCE(uc.number_override,
                       (SELECT COUNT(*) FROM user_cards uc2 WHERE uc2.obtained_at <= uc.obtained_at)) AS drop_number
            FROM user_cards uc
            JOIN cards c ON c.id = uc.card_id
            WHERE uc.user_id = ? AND uc.voided = 0 AND c.rarity != \'diamond\'
              AND uc.listed_price IS NULL AND uc.swap_listed = 0 AND uc.staked_at IS NULL
              AND uc.pvp_round_id IS NULL AND uc.pinned_at IS NULL
              AND NOT EXISTS (SELECT 1 FROM number_giveaways ng WHERE ng.user_card_id = uc.id AND ng.drawn_at IS NULL)
            """,
            (admin_id,),
        ).fetchall()
        if not rows:
            raise CardBatchGiveawayError("нет свободных карт (кроме diamond) для розыгрыша")
        draw_at = (datetime.now(timezone.utc) + timedelta(hours=duration_hours)).isoformat()
        now = _now()
        cur = conn.execute(
            "INSERT INTO card_batch_giveaways (admin_id, total_cards, created_at, draw_at) VALUES (?, ?, ?, ?)",
            (admin_id, len(rows), now, draw_at),
        )
        batch_id = cur.lastrowid
        cards = []
        for r in rows:
            conn.execute(
                "INSERT INTO number_giveaways (admin_id, user_card_id, number, card_name, card_rarity, "
                "card_filename, created_at, draw_at, batch_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (admin_id, r["user_card_id"], r["drop_number"], r["name"], r["rarity"], r["filename"], now, draw_at, batch_id),
            )
            cards.append({"user_card_id": r["user_card_id"], "name": r["name"], "rarity": r["rarity"], "filename": r["filename"]})
        return {"batch_id": batch_id, "draw_at": draw_at, "cards": cards}


def set_card_batch_giveaway_message(batch_id: int, chat_id: int, message_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE card_batch_giveaways SET chat_id = ?, message_id = ? WHERE id = ?",
            (chat_id, message_id, batch_id),
        )


def join_card_batch_giveaway(batch_id: int, user_id: int) -> str:
    """Same return convention as join_number_giveaway(): \'joined\', \'already_joined\',
    \'drawn\', \'not_found\', \'is_admin\'."""
    with get_conn() as conn:
        giveaway = conn.execute(
            "SELECT admin_id, drawn_at FROM card_batch_giveaways WHERE id = ?", (batch_id,)
        ).fetchone()
        if giveaway is None:
            return "not_found"
        if giveaway["admin_id"] == user_id:
            return "is_admin"
        if giveaway["drawn_at"] is not None:
            return "drawn"
        existing = conn.execute(
            "SELECT 1 FROM card_batch_giveaway_entries WHERE batch_id = ? AND user_id = ?",
            (batch_id, user_id),
        ).fetchone()
        if existing:
            return "already_joined"
        conn.execute(
            "INSERT INTO card_batch_giveaway_entries (batch_id, user_id, joined_at) VALUES (?, ?, ?)",
            (batch_id, user_id, _now()),
        )
        return "joined"


def get_card_batch_giveaway(batch_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM card_batch_giveaways WHERE id = ?", (batch_id,)).fetchone()
        return dict(row) if row else None


def count_card_batch_giveaway_entries(batch_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM card_batch_giveaway_entries WHERE batch_id = ?", (batch_id,)
        ).fetchone()[0]


def get_due_card_batch_giveaways() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM card_batch_giveaways WHERE drawn_at IS NULL AND draw_at <= ?", (_now(),)
        ).fetchall()
        return [dict(row) for row in rows]


def draw_card_batch_giveaway(batch_id: int) -> dict:
    """Splits every card at stake among whoever joined so EVERYONE gets at least one
    card (as long as there are at least as many cards as participants -- 312 cards vs
    a handful of people, the normal case here): first hands out one card per
    participant (a random pairing), then assigns every remaining card to a uniformly
    random participant, so the EXTRA amount past 1 is random and uneven -- some end
    up with more than others, nobody\'s left with zero. If there are somehow MORE
    participants than cards, an even split is impossible, so that many random
    participants each get one card and the rest get none (the fewer-cards-than-people
    edge case). Same per-card safety checks as draw_number_giveaway() (skips a card
    if it somehow became busy/gone in the meantime instead of failing the whole
    batch). Returns {"results": [{"card": {...}, "winner": {...}|None, "transferred":
    bool, "reason": str|None}], "total_entries": int}."""
    with get_conn() as conn:
        card_rows = list(conn.execute(
            "SELECT * FROM number_giveaways WHERE batch_id = ? AND drawn_at IS NULL", (batch_id,)
        ).fetchall())
        entries = [
            dict(row) for row in conn.execute(
                "SELECT u.telegram_id, u.username, u.first_name FROM card_batch_giveaway_entries be "
                "JOIN users u ON u.telegram_id = be.user_id WHERE be.batch_id = ?",
                (batch_id,),
            ).fetchall()
        ]
        random.shuffle(card_rows)
        winners_by_giveaway_id = {}
        if entries:
            shuffled_entrants = entries[:]
            random.shuffle(shuffled_entrants)
            guaranteed = min(len(card_rows), len(shuffled_entrants))
            for i in range(guaranteed):
                winners_by_giveaway_id[card_rows[i]["id"]] = shuffled_entrants[i]
            for extra_card in card_rows[guaranteed:]:
                winners_by_giveaway_id[extra_card["id"]] = random.choice(entries)
        results = []
        for giveaway in card_rows:
            card = {"name": giveaway["card_name"], "rarity": giveaway["card_rarity"], "filename": giveaway["card_filename"]}
            winner = winners_by_giveaway_id.get(giveaway["id"])
            transferred = False
            reason = None
            if winner is not None:
                row = conn.execute(
                    "SELECT user_id, voided, listed_price, swap_listed, staked_at, pvp_round_id, pinned_at "
                    "FROM user_cards WHERE id = ?",
                    (giveaway["user_card_id"],),
                ).fetchone()
                if row is None or row["voided"] or row["user_id"] != giveaway["admin_id"]:
                    reason = "карта уже недоступна (продана/потрачена)"
                elif (row["listed_price"] is not None or row["swap_listed"] or row["staked_at"] is not None
                        or row["pvp_round_id"] is not None or row["pinned_at"] is not None):
                    reason = "карта сейчас занята (продажа/обмен/стейк/пвп/стена)"
                else:
                    _detach_number_on_card_transfer(conn, giveaway["user_card_id"])
                    conn.execute(
                        "UPDATE user_cards SET user_id = ?, listed_price = NULL, swap_listed = 0, "
                        "staked_at = NULL, pvp_round_id = NULL, number_override = NULL, pinned_at = NULL WHERE id = ?",
                        (winner["telegram_id"], giveaway["user_card_id"]),
                    )
                    transferred = True
            conn.execute("UPDATE number_giveaways SET drawn_at = ? WHERE id = ?", (_now(), giveaway["id"]))
            results.append({"card": card, "winner": winner, "transferred": transferred, "reason": reason})
        conn.execute("UPDATE card_batch_giveaways SET drawn_at = ? WHERE id = ?", (_now(), batch_id))
        return {"results": results, "total_entries": len(entries)}


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
    Also blocked while the copy is staked — it has to be pulled out of staking first.
    Raises ListingPriceTooLow if price_gems is below this card's rarity floor (see
    get_min_listing_price())."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT uc.user_id, uc.swap_listed, uc.staked_at, uc.pvp_round_id, uc.pinned_at, c.rarity, "
            "(SELECT 1 FROM number_giveaways ng WHERE ng.user_card_id = uc.id AND ng.drawn_at IS NULL) AS in_giveaway "
            "FROM user_cards uc JOIN cards c ON c.id = uc.card_id WHERE uc.id = ?",
            (user_card_id,),
        ).fetchone()
        if row is None or row["user_id"] != seller_id:
            return False
        if row["swap_listed"] or row["staked_at"] is not None or row["pvp_round_id"] is not None or row["pinned_at"] is not None or row["in_giveaway"]:
            return False
        min_price = get_min_listing_price(row["rarity"])
        if price_gems < min_price:
            raise ListingPriceTooLow(min_price)
        conn.execute(
            "UPDATE user_cards SET listed_price = ?, listed_at = ? WHERE id = ?",
            (price_gems, _now(), user_card_id),
        )
        return True


def unlist_card(user_card_id: int, seller_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT user_id FROM user_cards WHERE id = ?", (user_card_id,)).fetchone()
        if row is None or row["user_id"] != seller_id:
            return False
        conn.execute("UPDATE user_cards SET listed_price = NULL, listed_at = NULL WHERE id = ?", (user_card_id,))
        conn.execute(
            "UPDATE market_offers SET status = 'cancelled' WHERE user_card_id = ? AND status = 'pending'",
            (user_card_id,),
        )
        return True


def get_market_listings() -> list[dict]:
    """All cards currently for sale, newest-LISTED first (listed_at — see list_card();
    NOT the same as the card's own obtain time, which is what uc.id would sort by)."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT uc.id AS user_card_id, uc.listed_price, uc.listed_at, uc.user_id AS seller_id,
                   c.id AS card_id, c.filename,
                   COALESCE(uc.custom_name, c.name) AS name,
                   COALESCE(uc.custom_rarity, c.rarity) AS rarity,
                   uc.custom_name AS custom_name,
                   u.username, u.first_name,
                   COALESCE(uc.number_override,
                       (SELECT COUNT(*) FROM user_cards uc2 WHERE uc2.obtained_at <= uc.obtained_at)) AS drop_number
            FROM user_cards uc
            JOIN cards c ON c.id = uc.card_id
            JOIN users u ON u.telegram_id = uc.user_id
            WHERE uc.listed_price IS NOT NULL
            ORDER BY uc.listed_at DESC
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
        _detach_number_on_card_transfer(conn, user_card_id)
        conn.execute(
            "UPDATE user_cards SET user_id = ?, listed_price = NULL, listed_at = NULL, number_override = NULL, pinned_at = NULL WHERE id = ?",
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
    or None if the listing doesn't exist / isn't for sale / buyer == seller. Raises
    ListingPriceTooLow if price_gems is below this card's rarity floor (see
    get_min_listing_price())."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT uc.user_id AS seller_id, uc.listed_price, c.filename, c.name, c.rarity
            FROM user_cards uc JOIN cards c ON c.id = uc.card_id
            WHERE uc.id = ?
            """,
            (user_card_id,),
        ).fetchone()
        if row is None or row["listed_price"] is None or row["seller_id"] == buyer_id:
            return None
        min_price = get_min_listing_price(row["rarity"])
        if price_gems < min_price:
            raise ListingPriceTooLow(min_price)
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
        _detach_number_on_card_transfer(conn, offer["user_card_id"])
        conn.execute(
            "UPDATE user_cards SET user_id = ?, listed_price = NULL, number_override = NULL, pinned_at = NULL WHERE id = ?",
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
            "SELECT user_id, listed_price, swap_listed, staked_at, pvp_round_id, pinned_at, "
            "(SELECT 1 FROM number_giveaways ng WHERE ng.user_card_id = user_cards.id AND ng.drawn_at IS NULL) AS in_giveaway "
            "FROM user_cards WHERE id = ?", (user_card_id,)
        ).fetchone()
        if row is None or row["user_id"] != seller_id:
            return False
        if row["listed_price"] is not None or row["swap_listed"] or row["staked_at"] is not None or row["pvp_round_id"] is not None or row["pinned_at"] is not None or row["in_giveaway"]:
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
            SELECT uc.id AS user_card_id, uc.user_id AS seller_id, c.id AS card_id, c.filename,
                   COALESCE(uc.custom_name, c.name) AS name,
                   COALESCE(uc.custom_rarity, c.rarity) AS rarity,
                   uc.custom_name AS custom_name,
                   COALESCE(uc.number_override,
                       (SELECT COUNT(*) FROM user_cards uc2 WHERE uc2.obtained_at <= uc.obtained_at)) AS drop_number
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

# The fee always equals ONE day's rate, so a staked card breaks even on day 1 and every day
# after that is pure profit — with no cap that's an unlimited money glitch (stake once, get
# paid forever). MAX_STAKE_DAYS turns it into a fixed-length term deposit: once a card has
# paid out this many days, settle_staking() auto-unstakes it — the player has to manually
# stake_card() (and pay the fee again) to keep earning. MAX_STAKED_CARDS caps how many cards
# can be staked AT ONCE, so the payout can't be scaled up without limit either.
MAX_STAKE_DAYS = 20
MAX_STAKED_CARDS = 5

# The one-time entry fee used to be exactly 1 day rate against a MAX_STAKE_DAYS=20
# payout -- a flat, risk-free 2000% return (+1900% net) on every rarity, wildly out of
# line with craft/burn/cases (~90-180% net, all with real risk of losing the card or
# gems). Per request, the fee is now several days worth instead of one, bringing the
# net return down closer to the rest of the economy while leaving the 20-day term and
# the payout rates themselves untouched.
STAKE_ENTRY_FEE_MULTIPLIER = 4  # fee = 4 days rate instead of 1 -- entry fee increased by 300 percent


def settle_staking(user_id: int) -> int:
    """Credits STAKE_DAILY_RATES[rarity] gems for every full 24h period elapsed since
    each of the user's staked cards was staked (or last paid out), advancing that card's
    clock forward by exactly that many whole days so partial progress toward the next
    payout is never lost or double-paid. Called from _authenticate() on every API request
    so staking pays out passively with no background job. Payouts are capped at
    MAX_STAKE_DAYS — once a card reaches that many paid days, it's auto-unstaked (credited
    for exactly MAX_STAKE_DAYS, not a day more) and goes back to being a normal owned card;
    the player has to stake_card() it again, paying the fee again, to keep earning. Returns
    gems credited this call."""
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
                daily_rate = STAKE_DAILY_RATES.get(row["rarity"] or "bronze", 5)
                if full_days >= MAX_STAKE_DAYS:
                    # Term's up — pay out exactly the cap, then release the card. Any extra
                    # elapsed time beyond the cap earns nothing further (same idea as a bank
                    # term deposit that just sits there, no longer accruing, until renewed).
                    total_credited += MAX_STAKE_DAYS * daily_rate
                    conn.execute("UPDATE user_cards SET staked_at = NULL WHERE id = ?", (row["id"],))
                else:
                    new_staked_at = staked_at + timedelta(seconds=full_days * STAKE_PERIOD_SECONDS)
                    conn.execute(
                        "UPDATE user_cards SET staked_at = ? WHERE id = ?",
                        (new_staked_at.isoformat(), row["id"]),
                    )
                    total_credited += full_days * daily_rate
        if total_credited:
            conn.execute(
                "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ?, "
                "staking_gems_earned = staking_gems_earned + ? WHERE telegram_id = ?",
                (total_credited, total_credited, total_credited, user_id),
            )
    return total_credited


class StakeLimitReached(Exception):
    """Raised by stake_card() when the player already has MAX_STAKED_CARDS cards staked at
    once."""


def stake_card(user_card_id: int, owner_id: int) -> bool:
    """Puts one owned copy into staking, after charging an upfront fee equal to that
    card's own daily staking rate (STAKE_DAILY_RATES). Blocked if it's already staked, or
    currently listed for sale/swap (unlist first). Raises InsufficientGems if the fee
    can't be covered, StakeLimitReached if the player already has MAX_STAKED_CARDS staked."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT uc.user_id, uc.listed_price, uc.swap_listed, uc.staked_at, uc.pvp_round_id, c.rarity, "
            "(SELECT 1 FROM number_giveaways ng WHERE ng.user_card_id = uc.id AND ng.drawn_at IS NULL) AS in_giveaway "
            "FROM user_cards uc JOIN cards c ON c.id = uc.card_id WHERE uc.id = ?",
            (user_card_id,),
        ).fetchone()
        if row is None or row["user_id"] != owner_id:
            return False
        if row["listed_price"] is not None or row["swap_listed"] or row["staked_at"] is not None or row["pvp_round_id"] is not None or row["in_giveaway"]:
            return False
        staked_count = conn.execute(
            "SELECT COUNT(*) AS n FROM user_cards WHERE user_id = ? AND staked_at IS NOT NULL",
            (owner_id,),
        ).fetchone()["n"]
        if staked_count >= MAX_STAKED_CARDS:
            raise StakeLimitReached()
        fee = STAKE_ENTRY_FEE_MULTIPLIER * STAKE_DAILY_RATES.get(row["rarity"] or "bronze", 5)
        gems_row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (owner_id,)).fetchone()
        if gems_row is None or gems_row["gems"] < fee:
            raise InsufficientGems()
        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (fee, owner_id))
        conn.execute("UPDATE user_cards SET staked_at = ? WHERE id = ?", (_now(), user_card_id))
        if not (ADMIN_ID and str(owner_id) == str(ADMIN_ID)):
            _bump_counter(conn, "stake")
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

        offered_cards = []
        for oid in offered_user_card_ids:
            row = conn.execute(
                """
                SELECT uc.user_id, uc.listed_price, uc.swap_listed, uc.staked_at, uc.pvp_round_id, c.name, c.rarity
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
            offered_cards.append({"name": row["name"] or "картинка", "rarity": row["rarity"] or "bronze"})

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
            "offered_cards": offered_cards,
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

        _detach_number_on_card_transfer(conn, offer["user_card_id"])
        conn.execute(
            "UPDATE user_cards SET user_id = ?, swap_listed = 0, number_override = NULL, pinned_at = NULL WHERE id = ?",
            (offer["buyer_id"], offer["user_card_id"]),
        )
        for oid in offered_ids:
            _detach_number_on_card_transfer(conn, oid)
            conn.execute(
                "UPDATE user_cards SET user_id = ?, swap_listed = 0, number_override = NULL, pinned_at = NULL WHERE id = ?",
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
            SELECT uc.user_id, uc.listed_price, uc.swap_listed, uc.staked_at, uc.pvp_round_id, c.filename, c.name,
                   (SELECT 1 FROM number_giveaways ng WHERE ng.user_card_id = uc.id AND ng.drawn_at IS NULL) AS in_giveaway
            FROM user_cards uc JOIN cards c ON c.id = uc.card_id
            WHERE uc.id = ?
            """,
            (user_card_id,),
        ).fetchone()
        if row is None or row["user_id"] != from_user_id:
            return None
        if row["listed_price"] is not None or row["swap_listed"] or row["staked_at"] is not None or row["pvp_round_id"] is not None or row["in_giveaway"]:
            return None
        gems_row = conn.execute("SELECT gems FROM users WHERE telegram_id = ?", (from_user_id,)).fetchone()
        if gems_row is None or gems_row["gems"] < TRANSFER_FEE_GEMS:
            raise InsufficientGems()
        conn.execute("UPDATE users SET gems = gems - ? WHERE telegram_id = ?", (TRANSFER_FEE_GEMS, from_user_id))
        _detach_number_on_card_transfer(conn, user_card_id)
        conn.execute(
            "UPDATE user_cards SET user_id = ?, listed_price = NULL, transfer_pending = 0, number_override = NULL, pinned_at = NULL WHERE id = ?",
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


# ---------------------------------------------------------------------------
# Referral race (/ref leaderboard + one-time scheduled announcement) — a stricter,
# harder-to-game count than get_referral_count() above: a referral only counts here
# once the referred player has (a) farmed at least one card (proves a real person,
# same anti-bot signal as the gems reward) and (b) joined PUBLIC_CHAT. (b) can only
# be checked by calling Telegram's getChatMember, so bot.py does that check and
# calls mark_chat_verified() once confirmed — get_unverified_ref_candidates() tells
# it who's still worth checking (already-farmed, not yet confirmed in chat).
# ---------------------------------------------------------------------------

def get_unverified_ref_candidates(limit: int = 300) -> list[int]:
    """Referred players who've farmed at least once but aren't chat-verified yet —
    i.e. worth spending a getChatMember call on. Capped per call so a large backlog
    (e.g. right after this feature is deployed) can't make one /ref invocation or
    scheduler tick block for too long; whatever's left over is picked up next time."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT telegram_id FROM users "
            "WHERE ref_by IS NOT NULL AND chat_member_verified = 0 "
            "AND EXISTS (SELECT 1 FROM user_cards WHERE user_cards.user_id = users.telegram_id) "
            "LIMIT ?",
            (limit,),
        ).fetchall()
        return [row["telegram_id"] for row in rows]


def mark_chat_verified(user_id: int) -> None:
    """Marks the referred player as confirmed-in-PUBLIC_CHAT. Gems for a referral now
    require BOTH a first farm (see farm() -- already checked before this is ever
    called, since get_unverified_ref_candidates() only returns already-farmed players)
    AND joining PUBLIC_CHAT (this call). If a reward was left queued in
    ref_reward_pending because chat membership wasn't confirmed yet at farm time, THIS
    is what fires it -- joining the chat was the last box left to tick."""
    payout = None
    with get_conn() as conn:
        conn.execute("UPDATE users SET chat_member_verified = 1 WHERE telegram_id = ?", (user_id,))
        row = conn.execute(
            "SELECT ref_by, ref_reward_pending, username, first_name FROM users WHERE telegram_id = ?",
            (user_id,),
        ).fetchone()
        if row and row["ref_by"] is not None and row["ref_reward_pending"]:
            reward_amount = row["ref_reward_pending"]
            conn.execute(
                "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? WHERE telegram_id = ?",
                (reward_amount, reward_amount, row["ref_by"]),
            )
            conn.execute("UPDATE users SET ref_reward_pending = 0 WHERE telegram_id = ?", (user_id,))
            who_name = f"@{row['username']}" if row["username"] else (row["first_name"] or "Реферал")
            payout = {"referrer_id": row["ref_by"], "who_name": who_name, "amount": reward_amount}
    if payout:
        set_referral_notice(payout["referrer_id"], payout["who_name"], payout["amount"])


def get_ref_leaderboard(limit: int = 5, exclude_id: int | None = None) -> list[dict]:
    """Top referrers by qualified (farmed + chat-verified) referral count, highest first.
    exclude_id (bot.py passes ADMIN_ID) leaves one telegram_id out of the ranking
    entirely — used to keep the dev's own test/admin account out of the public race."""
    with get_conn() as conn:
        excl_placeholders = ",".join("?" for _ in LEADERBOARD_EXCLUDED_USERNAMES)
        query = (
            "SELECT u.telegram_id AS telegram_id, u.username AS username, u.first_name AS first_name, "
            "COUNT(r.telegram_id) AS n "
            "FROM users u JOIN users r ON r.ref_by = u.telegram_id "
            "WHERE r.chat_member_verified = 1 "
            f"AND LOWER(COALESCE(u.username, '')) NOT IN ({excl_placeholders})"
        )
        params: list = list(LEADERBOARD_EXCLUDED_USERNAMES)
        if exclude_id is not None:
            query += " AND u.telegram_id != ?"
            params.append(exclude_id)
        query += " GROUP BY u.telegram_id ORDER BY n DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def has_ref_race_been_announced() -> bool:
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM ref_race_announced LIMIT 1").fetchone() is not None


def mark_ref_race_announced() -> None:
    with get_conn() as conn:
        conn.execute("INSERT INTO ref_race_announced (announced_at) VALUES (?)", (_now(),))


def has_ref_race_countdown_been_sent(stage: str) -> bool:
    with get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM ref_race_countdown_sent WHERE stage = ?", (stage,)
        ).fetchone() is not None


def mark_ref_race_countdown_sent(stage: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO ref_race_countdown_sent (stage, sent_at) VALUES (?, ?)",
            (stage, _now()),
        )


def set_referral_notice(referrer_id: int, who_name: str, amount: int) -> None:
    """Called from /api/farm the moment a referral reward is credited — stores the
    referred player's display name AND the (now graduated, not always 25) gem amount
    so the referrer's own client can pop an in-app 'you got N gems' modal next time it
    loads, instead of a bot DM."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET referral_reward_notice = ? WHERE telegram_id = ?",
            (json.dumps({"name": who_name, "amount": amount}), referrer_id),
        )


def get_and_clear_referral_notice(user_id: int) -> dict | None:
    """Read-once: returns the pending referral-reward {name, amount} (if any) and
    clears it in the same call, so /api/auth shows the popup exactly once per reward."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT referral_reward_notice FROM users WHERE telegram_id = ?", (user_id,)
        ).fetchone()
        raw = row["referral_reward_notice"] if row else None
        if raw:
            conn.execute(
                "UPDATE users SET referral_reward_notice = NULL WHERE telegram_id = ?", (user_id,)
            )
            try:
                return json.loads(raw)
            except (ValueError, TypeError):
                return None
        return None


def get_total_farmed(exclude_id: int | None = None) -> int:
    """Global count of every farm drop ever, across all users, MINUS anything since
    burned away (voided=1) — this is the "Всего" figure shown in the app, and it can go
    DOWN now that burn_cards() exists.

    exclude_id: when given, cards owned by that user are left out of the count. Used
    by get_admin_stats() to pass ADMIN_ID so /admin's "Карты" figure reflects real
    player usage only — every other caller leaves this unset and is unaffected."""
    with get_conn() as conn:
        if exclude_id is not None:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM user_cards WHERE voided = 0 AND user_id != ?",
                (exclude_id,),
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS n FROM user_cards WHERE voided = 0").fetchone()
        return row["n"]


def get_global_rarity_breakdown() -> dict:
    """How many cards of each rarity are currently on hand across ALL players combined
    (voided/burned-away rows excluded, same as get_total_farmed()) — the global counterpart
    to a single player's own inventory breakdown, shown when tapping the "Карты" figure on
    the Farm screen."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT c.rarity, COUNT(*) AS n FROM user_cards uc "
            "JOIN cards c ON c.id = uc.card_id WHERE uc.voided = 0 GROUP BY c.rarity"
        ).fetchall()
    counts = {r["rarity"]: r["n"] for r in rows}
    return {rarity: counts.get(rarity, 0) for rarity in ("bronze", "silver", "gold", "platinum", "diamond")}


# Accounts kept off every public leaderboard/ranking, whatever their stats say --
# the dev's own admin/test account plus family members who aren't part of the real
# competitive rankings. Matched by username (lowercase, no @) since that's stable
# across every leaderboard query, unlike an env-var telegram_id that only covers one
# account (ADMIN_ID). Used by get_leaderboard()/get_ref_leaderboard()/
# get_pvp_win_leaderboard() -- add a username here to hide that account from all
# three at once.
LEADERBOARD_EXCLUDED_USERNAMES = {"rzabeyda", "zzabeyda", "test_bot"}


# Just for the general/collection "Топы" screen (Карты/Гемы/Diamond-карты) -- kept
# separate from LEADERBOARD_EXCLUDED_USERNAMES because zzabeyda should still show up
# on the PvP/Aviator/Red&Black "game" leaderboards, just not here.
COLLECTION_TOPS_EXCLUDED_USERNAMES = LEADERBOARD_EXCLUDED_USERNAMES | {"zzabeyda"}


def get_leaderboard() -> list[dict]:
    """Players ranked by total cards owned, lifetime gems earned, and Diamond cards
    owned — for the 'Топы' screen, which lets the player switch between the rankings
    client-side. Unlike the market, identities are shown here on purpose: that's the
    whole point of a leaderboard. Returns everyone (no LIMIT) since the rankings can
    surface different people; the frontend slices each to its own top 50."""
    with get_conn() as conn:
        placeholders = ",".join("?" for _ in COLLECTION_TOPS_EXCLUDED_USERNAMES)
        rows = conn.execute(
            f"""
            SELECT u.telegram_id, u.username, u.first_name, u.photo_url,
                   COUNT(uc.id) AS total_cards, u.gems AS current_gems,
                   COUNT(CASE WHEN c.rarity = 'diamond' THEN 1 END) AS diamond_cards
            FROM users u
            LEFT JOIN user_cards uc ON uc.user_id = u.telegram_id AND uc.voided = 0
            LEFT JOIN cards c ON c.id = uc.card_id
            WHERE LOWER(COALESCE(u.username, '')) NOT IN ({placeholders})
            GROUP BY u.telegram_id
            ORDER BY total_cards DESC, u.telegram_id ASC
            """,
            list(COLLECTION_TOPS_EXCLUDED_USERNAMES),
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

PVP_LOCK_SECONDS = 20
# Staking used to close a few seconds before the round resolved so a card couldn't be
# thrown in right at the wire — removed by request, betting is now allowed right up to
# the round actually resolving. Kept at 0 (not deleted) so join_pvp_round()'s "already
# past lock_at" guard still blocks joining a round that has actually finished.
PVP_JOIN_CUTOFF_SECONDS = 0
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


PVP_TEST_BOT_ID = -1  # negative -- can never collide with a real Telegram user id


def join_pvp_test_bot() -> dict:
    """Admin-only test helper (/pvpbot in bot.py): makes a fake bot account join the
    CURRENT open PvP lobby round with one free card, so the dev can trigger a 2-player
    round (and the reveal animation) without needing a second real account. Creates the
    bot account and hands it a fresh card the first time; reuses both on later calls."""
    with get_conn() as conn:
        row = conn.execute("SELECT telegram_id FROM users WHERE telegram_id = ?", (PVP_TEST_BOT_ID,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users (telegram_id, username, first_name, gems, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (PVP_TEST_BOT_ID, "test_bot", "Тест-бот", 999999, _now()),
            )
        ucid_row = conn.execute(
            "SELECT id FROM user_cards WHERE user_id = ? AND listed_price IS NULL AND swap_listed = 0 "
            "AND staked_at IS NULL AND pvp_round_id IS NULL AND pinned_at IS NULL LIMIT 1",
            (PVP_TEST_BOT_ID,),
        ).fetchone()
        if ucid_row:
            ucid = ucid_row["id"]
        else:
            card_row = conn.execute("SELECT id FROM cards WHERE is_active = 1 ORDER BY id LIMIT 1").fetchone()
            if card_row is None:
                raise RuntimeError("нет ни одной карты в каталоге -- нечего дать тест-боту")
            cur = conn.execute(
                "INSERT INTO user_cards (user_id, card_id, obtained_at) VALUES (?, ?, ?)",
                (PVP_TEST_BOT_ID, card_row["id"], _now()),
            )
            ucid = cur.lastrowid
    return join_pvp_round(PVP_TEST_BOT_ID, [ucid])


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
                "SELECT user_id, listed_price, swap_listed, staked_at, pvp_round_id, pinned_at, card_id, "
                "(SELECT 1 FROM number_giveaways ng WHERE ng.user_card_id = user_cards.id AND ng.drawn_at IS NULL) AS in_giveaway "
                "FROM user_cards WHERE id = ?",
                (ucid,),
            ).fetchone()
            if row is None or row["user_id"] != user_id:
                raise PvpCardNotOwned()
            if (row["listed_price"] is not None or row["swap_listed"] or row["staked_at"] is not None
                    or row["pvp_round_id"] is not None or row["pinned_at"] is not None or row["in_giveaway"]):
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


PVP_INVITE_COOLDOWN_SECONDS = 300  # keeps "Позвать игрока" from spamming PUBLIC_CHAT


def is_in_open_pvp_round(user_id: int) -> bool:
    """True if user_id currently has at least one card staked in the CURRENT open PvP
    lobby round (i.e. get_pvp_state()'s you_joined for this user) -- used to gate
    "Позвать игрока" so only someone who has actually staked cards can ping the chat."""
    with get_conn() as conn:
        round_id = _get_or_create_open_round(conn)
        row = conn.execute(
            "SELECT 1 FROM pvp_entries WHERE round_id = ? AND user_id = ? LIMIT 1",
            (round_id, user_id),
        ).fetchone()
        return row is not None


def get_open_pvp_participant_count() -> int:
    """Number of DISTINCT players currently staked in the current open PvP lobby round
    -- used to gate "Позвать игрока": once the bank already has 2+ people it doesn't
    need more callers, so the button stops working past that point."""
    with get_conn() as conn:
        round_id = _get_or_create_open_round(conn)
        row = conn.execute(
            "SELECT COUNT(DISTINCT user_id) AS n FROM pvp_entries WHERE round_id = ?",
            (round_id,),
        ).fetchone()
        return row["n"]


def try_pvp_invite(user_id: int) -> dict:
    """Claims the right to post one "зовёт в PvP" ping into PUBLIC_CHAT, gated by
    PVP_INVITE_COOLDOWN_SECONDS per player (not global -- each player gets their own
    cooldown clock). Returns {"ok": True} and stamps last_pvp_invite_at the instant
    this call is allowed to go through (bot.py sends the actual chat message right
    after, so the stamp and the send happen back to back); {"ok": False,
    "seconds_left": int} if the player is still on cooldown -- the caller shows that
    instead of posting anything."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_pvp_invite_at FROM users WHERE telegram_id = ?", (user_id,)
        ).fetchone()
        if row and row["last_pvp_invite_at"]:
            elapsed = (datetime.now(timezone.utc) - _parse_utc(row["last_pvp_invite_at"])).total_seconds()
            if elapsed < PVP_INVITE_COOLDOWN_SECONDS:
                return {"ok": False, "seconds_left": int(PVP_INVITE_COOLDOWN_SECONDS - elapsed)}
        conn.execute("UPDATE users SET last_pvp_invite_at = ? WHERE telegram_id = ?", (_now(), user_id))
    return {"ok": True}


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


def get_last_resolved_pvp_round(round_id: int | None = None) -> dict | None:
    """The most recently resolved PvP round's summary — so the lobby can show a 'last
    result' banner even to players who weren't watching when it happened (this is the
    only place the actual winner reveal lives; the client shows it once per round_id).
    Includes a full `participants` breakdown (same shape as get_pvp_state's), rebuilt
    from the historical pvp_entries rows, so the client can redraw the exact wheel the
    round was decided on for the spin-to-a-winner reveal animation. Pass round_id to
    look up one specific resolved round instead of "whatever resolved most recently"
    -- used by the "Поделиться" share endpoint, which must re-derive its message from
    a round the player actually saw rather than trusting the client's own claim."""
    with get_conn() as conn:
        if round_id is not None:
            round_row = conn.execute(
                "SELECT id, winner_id, resolved_at FROM pvp_rounds WHERE status = 'resolved' AND id = ?",
                (round_id,),
            ).fetchone()
        else:
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

    masked_winner_username = _mask_username(winner["username"]) if winner is not None else None
    if masked_winner_username:
        winner_name = masked_winner_username
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
            SELECT pr.id, pr.winner_id, pr.resolved_at, pr.winner_win_pct,
                   w.username AS winner_username, w.first_name AS winner_first_name
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
            masked_winner_username = _mask_username(r["winner_username"])
            winner_name = (
                masked_winner_username if masked_winner_username
                else (r["winner_first_name"] or "игрок")
            )
            out.append({
                "round_id": r["id"],
                "resolved_at": r["resolved_at"],
                "winner_name": winner_name,
                "total_cards": total_cards,
                "total_players": total_players,
                "winner_win_pct": r["winner_win_pct"],
            })
    return out


def get_redblack_round(round_id: int) -> dict | None:
    """One Red&Black round by id, joined with the player's username/first_name — used
    both by the "Поделиться" share endpoint (to verify ownership + build the message
    server-side, never trusting the client's own claimed result) and could back a
    per-round lookup elsewhere later."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT rr.*, u.username, u.first_name FROM redblack_rounds rr "
            "JOIN users u ON u.telegram_id = rr.user_id WHERE rr.id = ?",
            (round_id,),
        ).fetchone()
        return dict(row) if row else None


def get_redblack_history(limit: int = 50) -> list[dict]:
    """Every Red&Black round ever played, newest first — for the "История" panel,
    same convention as get_pvp_history()/get_market_history()."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT rr.id, rr.user_id, rr.bet, rr.choice, rr.result, rr.won, rr.payout, rr.created_at, "
            "u.username, u.first_name "
            "FROM redblack_rounds rr JOIN users u ON u.telegram_id = rr.user_id "
            "ORDER BY rr.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_redblack_leaderboard(limit: int = 10) -> list[dict]:
    """Top players by total net gems won across every Red&Black round they've played
    (SUM(payout - bet) -- a loss round contributes -bet, a win round contributes
    +bet), highest first. Same LEADERBOARD_EXCLUDED_USERNAMES convention as every
    other leaderboard in the game."""
    with get_conn() as conn:
        excl_placeholders = ",".join("?" for _ in LEADERBOARD_EXCLUDED_USERNAMES)
        rows = conn.execute(
            "SELECT rr.user_id AS telegram_id, u.username AS username, u.first_name AS first_name, "
            "SUM(rr.payout - rr.bet) AS net_profit, COUNT(*) AS rounds_played "
            "FROM redblack_rounds rr JOIN users u ON u.telegram_id = rr.user_id "
            f"WHERE LOWER(COALESCE(u.username, '')) NOT IN ({excl_placeholders}) "
            "GROUP BY rr.user_id ORDER BY net_profit DESC LIMIT ?",
            list(LEADERBOARD_EXCLUDED_USERNAMES) + [limit],
        ).fetchall()
        return [dict(r) for r in rows]


def get_aviator_round(round_id: int) -> dict | None:
    """One Aviator round by id, joined with the player's username/first_name -- same
    role as get_redblack_round() (share endpoint's server-side source of truth)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT ar.*, u.username, u.first_name FROM aviator_rounds ar "
            "JOIN users u ON u.telegram_id = ar.user_id WHERE ar.id = ?",
            (round_id,),
        ).fetchone()
        return dict(row) if row else None


def get_aviator_history(limit: int = 50) -> list[dict]:
    """Every resolved Aviator round (won or lost -- 'active' ones are still in
    flight and excluded), newest first, for the "История" panel. Covers BOTH the
    chat /go game and the in-app one -- same underlying table, same game, just two
    ways to play it, same convention count_active_aviator_rounds_for_user() already
    uses (chat_id IS NULL) to tell them apart when it matters."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ar.id, ar.user_id, ar.bet, ar.crash_point, ar.status, ar.cashout_multiplier, ar.created_at, "
            "u.username, u.first_name "
            "FROM aviator_rounds ar JOIN users u ON u.telegram_id = ar.user_id "
            "WHERE ar.status IN ('won', 'lost') "
            "ORDER BY ar.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_aviator_leaderboard(limit: int = 10) -> list[dict]:
    """Top players by total net gems won across every resolved Aviator round
    (won: +(bet*cashout_multiplier - bet), lost: -bet), highest first. Same
    LEADERBOARD_EXCLUDED_USERNAMES convention as every other leaderboard."""
    with get_conn() as conn:
        excl_placeholders = ",".join("?" for _ in LEADERBOARD_EXCLUDED_USERNAMES)
        rows = conn.execute(
            "SELECT ar.user_id AS telegram_id, u.username AS username, u.first_name AS first_name, "
            "SUM(CASE WHEN ar.status = 'won' THEN CAST(ROUND(ar.bet * ar.cashout_multiplier) AS INTEGER) - ar.bet "
            "ELSE -ar.bet END) AS net_profit, "
            "COUNT(*) AS rounds_played "
            "FROM aviator_rounds ar JOIN users u ON u.telegram_id = ar.user_id "
            f"WHERE ar.status IN ('won', 'lost') AND LOWER(COALESCE(u.username, '')) NOT IN ({excl_placeholders}) "
            "GROUP BY ar.user_id ORDER BY net_profit DESC LIMIT ?",
            list(LEADERBOARD_EXCLUDED_USERNAMES) + [limit],
        ).fetchall()
        return [dict(r) for r in rows]


def get_pvp_win_leaderboard(limit: int = 10, exclude_id: int | None = None) -> list[dict]:
    """Top players by total resolved PvP round wins, highest first. exclude_id leaves
    one telegram_id out entirely (used to keep the dev's own account off the public
    leaderboard, same convention as get_ref_leaderboard())."""
    with get_conn() as conn:
        excl_placeholders = ",".join("?" for _ in LEADERBOARD_EXCLUDED_USERNAMES)
        query = (
            "SELECT pr.winner_id AS telegram_id, u.username AS username, u.first_name AS first_name, "
            "COUNT(*) AS wins "
            "FROM pvp_rounds pr JOIN users u ON u.telegram_id = pr.winner_id "
            "WHERE pr.status = 'resolved' AND pr.winner_id IS NOT NULL "
            f"AND LOWER(COALESCE(u.username, '')) NOT IN ({excl_placeholders})"
        )
        params: list = list(LEADERBOARD_EXCLUDED_USERNAMES)
        if exclude_id is not None:
            query += " AND pr.winner_id != ?"
            params.append(exclude_id)
        query += " GROUP BY pr.winner_id ORDER BY wins DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def get_top_stakers(limit: int = 10, exclude_id: int | None = None) -> list[dict]:
    """Top players by lifetime gems earned specifically from staking (staking_gems_earned
    -- separate from the general gems_earned, which mixes in every other gem source).
    Only reflects staking income credited after this counter was added. Same
    LEADERBOARD_EXCLUDED_USERNAMES convention as every other leaderboard."""
    with get_conn() as conn:
        excl_placeholders = ",".join("?" for _ in LEADERBOARD_EXCLUDED_USERNAMES)
        query = f"""
            SELECT telegram_id, username, first_name, staking_gems_earned
            FROM users
            WHERE staking_gems_earned > 0
            AND LOWER(COALESCE(username, '')) NOT IN ({excl_placeholders})
        """
        params: list = list(LEADERBOARD_EXCLUDED_USERNAMES)
        if exclude_id is not None:
            query += " AND telegram_id != ?"
            params.append(exclude_id)
        query += " ORDER BY staking_gems_earned DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def get_pvp_cards_won_leaderboard(limit: int = 10, exclude_id: int | None = None) -> list[dict]:
    """Top players by NET cards across all resolved PvP rounds -- cards captured from
    opponents (rounds they won) minus cards they themselves lost (rounds someone else
    won), not just a gross "cards captured" count. A winner's own staked cards
    returning to them never count on either side (pe.user_id != pr.winner_id in both
    the won and lost halves)."""
    with get_conn() as conn:
        excl_placeholders = ",".join("?" for _ in LEADERBOARD_EXCLUDED_USERNAMES)
        query = f"""
            WITH won AS (
                SELECT pr.winner_id AS user_id, COUNT(*) AS n
                FROM pvp_entries pe JOIN pvp_rounds pr ON pr.id = pe.round_id
                WHERE pr.status = 'resolved' AND pr.winner_id IS NOT NULL AND pe.user_id != pr.winner_id
                GROUP BY pr.winner_id
            ),
            lost AS (
                SELECT pe.user_id AS user_id, COUNT(*) AS n
                FROM pvp_entries pe JOIN pvp_rounds pr ON pr.id = pe.round_id
                WHERE pr.status = 'resolved' AND pr.winner_id IS NOT NULL AND pe.user_id != pr.winner_id
                GROUP BY pe.user_id
            )
            SELECT u.telegram_id AS telegram_id, u.username AS username, u.first_name AS first_name,
                   COALESCE(won.n, 0) - COALESCE(lost.n, 0) AS cards_won
            FROM users u
            LEFT JOIN won ON won.user_id = u.telegram_id
            LEFT JOIN lost ON lost.user_id = u.telegram_id
            WHERE (won.n IS NOT NULL OR lost.n IS NOT NULL)
            AND LOWER(COALESCE(u.username, '')) NOT IN ({excl_placeholders})
        """
        params: list = list(LEADERBOARD_EXCLUDED_USERNAMES)
        if exclude_id is not None:
            query += " AND u.telegram_id != ?"
            params.append(exclude_id)
        query += " ORDER BY cards_won DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def get_pvp_diamond_cards_won_leaderboard(limit: int = 10, exclude_id: int | None = None) -> list[dict]:
    """Same as get_pvp_cards_won_leaderboard() (net = captured - lost), restricted to
    diamond-rarity cards only (rarity at the time staked -- never changes after farming)."""
    with get_conn() as conn:
        excl_placeholders = ",".join("?" for _ in LEADERBOARD_EXCLUDED_USERNAMES)
        query = f"""
            WITH won AS (
                SELECT pr.winner_id AS user_id, COUNT(*) AS n
                FROM pvp_entries pe
                JOIN pvp_rounds pr ON pr.id = pe.round_id
                JOIN user_cards uc ON uc.id = pe.user_card_id
                JOIN cards c ON c.id = uc.card_id
                WHERE pr.status = 'resolved' AND pr.winner_id IS NOT NULL AND pe.user_id != pr.winner_id
                AND c.rarity = 'diamond'
                GROUP BY pr.winner_id
            ),
            lost AS (
                SELECT pe.user_id AS user_id, COUNT(*) AS n
                FROM pvp_entries pe
                JOIN pvp_rounds pr ON pr.id = pe.round_id
                JOIN user_cards uc ON uc.id = pe.user_card_id
                JOIN cards c ON c.id = uc.card_id
                WHERE pr.status = 'resolved' AND pr.winner_id IS NOT NULL AND pe.user_id != pr.winner_id
                AND c.rarity = 'diamond'
                GROUP BY pe.user_id
            )
            SELECT u.telegram_id AS telegram_id, u.username AS username, u.first_name AS first_name,
                   COALESCE(won.n, 0) - COALESCE(lost.n, 0) AS diamond_cards_won
            FROM users u
            LEFT JOIN won ON won.user_id = u.telegram_id
            LEFT JOIN lost ON lost.user_id = u.telegram_id
            WHERE (won.n IS NOT NULL OR lost.n IS NOT NULL)
            AND LOWER(COALESCE(u.username, '')) NOT IN ({excl_placeholders})
        """
        params: list = list(LEADERBOARD_EXCLUDED_USERNAMES)
        if exclude_id is not None:
            query += " AND u.telegram_id != ?"
            params.append(exclude_id)
        query += " ORDER BY diamond_cards_won DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


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
            # Winner's actual odds going into the draw (their weight / total weight),
            # rounded to a whole percent -- shown in the "История" log so it reads like
            # "rzabeyda забрал банк с 94% — 13 карт" instead of just the raw card count.
            winner_win_pct = round(weights_by_user[winner_id] / total * 100) if total else 0

            card_ids = [e["user_card_id"] for e in entries]
            for cid in card_ids:
                _detach_number_on_card_transfer(conn, cid)
            conn.executemany(
                "UPDATE user_cards SET user_id = ?, pvp_round_id = NULL, number_override = NULL, pinned_at = NULL WHERE id = ?",
                [(winner_id, cid) for cid in card_ids],
            )
            conn.execute(
                "UPDATE pvp_rounds SET status = 'resolved', winner_id = ?, resolved_at = ?, winner_win_pct = ? WHERE id = ?",
                (winner_id, now_iso, winner_win_pct, round_id),
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
        seen_rows = conn.execute(
            "SELECT last_seen_at FROM users WHERE last_seen_at IS NOT NULL"
        ).fetchall()
        farm_rows = conn.execute(
            "SELECT last_farm_at FROM users WHERE last_farm_at IS NOT NULL"
        ).fetchall()
    # "Active today" = last_seen_at (stamped on every /api/auth call, i.e. every app open)
    # falls on today's LOCAL (Tallinn) calendar date — same day-rollover convention used
    # for streaks/daily bonus elsewhere, not a raw 24h window.
    today_local_date = datetime.now(timezone.utc).astimezone(TALLINN_TZ).date()
    active_today = sum(
        1 for row in seen_rows
        if _parse_utc(row["last_seen_at"]).astimezone(TALLINN_TZ).date() == today_local_date
    )
    # Unique players who pressed "Фарм" today (last_farm_at is overwritten on every
    # farm(), not just their first of the day, so this is a simple "how many distinct
    # users have a most-recent farm falling on today's local date" count).
    farmers_today = sum(
        1 for row in farm_rows
        if _parse_utc(row["last_farm_at"]).astimezone(TALLINN_TZ).date() == today_local_date
    )
    # Reuse get_total_farmed() instead of a separate raw COUNT(*) here — that raw query
    # used to forget the "WHERE voided = 0" filter that burn_cards()/craft_card() rely on,
    # so /admin showed a higher, stale card count than the in-app "Карты" figure once any
    # burning/evolution had happened. Sharing the one function keeps them from drifting again.
    total_farmed = get_total_farmed(exclude_id=int(ADMIN_ID) if ADMIN_ID else None)
    stats = {
        "users": users, "cards": cards, "gems_total": gems_total,
        "total_farmed": total_farmed, "active_today": active_today,
        "farmers_today": farmers_today,
    }
    stats.update(get_action_counters())
    return stats


def get_card_by_id(card_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()


# ---------------------------------------------------------------------------
# Crypto withdrawal — "Продать Diamond карты за GRAM" (repurposes the old
# "Продать Гемы за Звёзды" button in the gems-choice overlay). Player picks a
# multiple of GRAM_CARDS_PER_UNIT of their own eligible Diamond cards and a wallet
# address; the admin (ADMIN_ID in bot.py) gets a DM with "Оплатить"/"Отменить"
# buttons, sends the crypto manually outside this system, then taps one of them.
#
# The selected cards are voided (held) the moment a request is created — same
# soft-destroy trick as burn_cards()/craft_card() failures — so they can't be
# listed/swapped/staked/double-spent while the request is pending. "Оплатить"
# leaves them voided for good (they left the game in exchange for the crypto
# already sent); "Отменить" un-voids exactly those cards, restoring them.
# ---------------------------------------------------------------------------

GRAM_CARDS_PER_UNIT = 10  # 10 Diamond cards = 1 payout unit
STARS_PER_UNIT = 100  # each unit now pays 100 Telegram Stars (was 1 GRAM back when this paid crypto)


class CryptoWithdrawalError(Exception):
    """Raised by request_crypto_withdrawal() for any invalid request: wrong/non-multiple
    card count, a missing wallet address, cards that aren't this user's own eligible
    (owned, Diamond, non-busy, non-voided) cards, or an already-pending request."""
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def get_pending_withdrawal(user_id: int) -> dict | None:
    """The caller's own currently-pending request, if any — used both to block a second
    simultaneous request and so the frontend can show "заявка на рассмотрении" instead of
    the picker."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM crypto_withdrawals WHERE user_id = ? AND status = 'pending' "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def request_crypto_withdrawal(user_id: int, user_card_ids: list[int], wallet_address: str = "") -> dict:
    # wallet_address is now optional/unused for real — this used to be a crypto (GRAM)
    # payout requiring an external wallet; it now pays out in Telegram Stars straight to
    # the user's own account, so there's nothing to collect from them here. Kept as a
    # column/param for backward compatibility with existing pending/history rows.
    wallet_address = (wallet_address or "").strip()

    count = len(user_card_ids)
    if count == 0 or count % GRAM_CARDS_PER_UNIT != 0 or len(set(user_card_ids)) != count:
        raise CryptoWithdrawalError(f"card count must be a positive multiple of {GRAM_CARDS_PER_UNIT}")

    if get_pending_withdrawal(user_id) is not None:
        raise CryptoWithdrawalError("you already have a pending withdrawal request")

    with get_conn() as conn:
        placeholders = ",".join("?" for _ in user_card_ids)
        rows = conn.execute(
            f"SELECT uc.id FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
            f"WHERE uc.id IN ({placeholders}) AND uc.user_id = ? AND c.rarity = 'diamond' "
            f"AND uc.listed_price IS NULL AND uc.swap_listed = 0 "
            f"AND uc.staked_at IS NULL AND uc.pvp_round_id IS NULL AND uc.voided = 0 "
            f"AND NOT EXISTS (SELECT 1 FROM number_giveaways ng WHERE ng.user_card_id = uc.id AND ng.drawn_at IS NULL)",
            (*user_card_ids, user_id),
        ).fetchall()
        if len(rows) != count:
            raise CryptoWithdrawalError("some selected cards aren't your own eligible Diamond cards")

        gram_amount = (count // GRAM_CARDS_PER_UNIT) * STARS_PER_UNIT
        now = _now()
        cur = conn.execute(
            "INSERT INTO crypto_withdrawals (user_id, card_count, gram_amount, wallet_address, status, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (user_id, count, gram_amount, wallet_address, now),
        )
        withdrawal_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO crypto_withdrawal_cards (withdrawal_id, user_card_id) VALUES (?, ?)",
            [(withdrawal_id, uc_id) for uc_id in user_card_ids],
        )
        for uc_id in user_card_ids:
            _release_name_on_card_void(conn, uc_id)
        conn.execute(
            f"UPDATE user_cards SET voided = 1, listed_price = NULL, swap_listed = 0, "
            f"staked_at = NULL, pvp_round_id = NULL, custom_name = NULL, custom_rarity = NULL WHERE id IN ({placeholders})",
            user_card_ids,
        )

    return {
        "withdrawal_id": withdrawal_id,
        "card_count": count,
        "gram_amount": gram_amount,
        "wallet_address": wallet_address,
    }


def get_withdrawal(withdrawal_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM crypto_withdrawals WHERE id = ?", (withdrawal_id,)).fetchone()
    return dict(row) if row else None


def admin_pay_withdrawal(withdrawal_id: int) -> dict | None:
    """Marks a pending request paid. Cards stay voided — they left the game for good, in
    exchange for the crypto the admin already sent manually outside this system. Returns
    None if the request no longer exists or isn't pending (already resolved)."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM crypto_withdrawals WHERE id = ?", (withdrawal_id,)).fetchone()
        if row is None or row["status"] != "pending":
            return None
        conn.execute(
            "UPDATE crypto_withdrawals SET status = 'paid', resolved_at = ? WHERE id = ?",
            (_now(), withdrawal_id),
        )
    return dict(row)


def admin_cancel_withdrawal(withdrawal_id: int) -> dict | None:
    """Marks a pending request cancelled and restores (un-voids) exactly the cards that
    were held for it. Returns None if the request no longer exists or isn't pending."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM crypto_withdrawals WHERE id = ?", (withdrawal_id,)).fetchone()
        if row is None or row["status"] != "pending":
            return None
        card_ids = [r["user_card_id"] for r in conn.execute(
            "SELECT user_card_id FROM crypto_withdrawal_cards WHERE withdrawal_id = ?", (withdrawal_id,)
        ).fetchall()]
        if card_ids:
            placeholders = ",".join("?" for _ in card_ids)
            conn.execute(f"UPDATE user_cards SET voided = 0 WHERE id IN ({placeholders})", card_ids)
        conn.execute(
            "UPDATE crypto_withdrawals SET status = 'cancelled', resolved_at = ? WHERE id = ?",
            (_now(), withdrawal_id),
        )
    return dict(row)


if __name__ == "__main__":
    init_db()
    print(f"DB initialized at {DB_PATH}")
