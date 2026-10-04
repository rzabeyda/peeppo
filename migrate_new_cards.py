"""
One-off migration: replace the OLD card catalog with the NEW "nft" set.

    python3 migrate_new_cards.py            # DRY RUN (default): does everything inside a
                                            # transaction, prints the plan, then ROLLS BACK
    python3 migrate_new_cards.py --apply    # backs the DB up, then does it for real

What it does (one transaction, so it is all-or-nothing):
  1. pays out staking income already earned by cards that are about to disappear
  2. every non-Obsidian, non-voided player card is voided (soft delete -- the same
     "voided=1" pattern burn/craft use, so history rows stay valid) and its owner is paid
     gems by rank: Bronze 25 / Silver 50 / Gold 150 / Platinum 600 / Diamond 3000
  3. listings, offers, blind-swap queue entries, open PvP entries on those cards are cleared
  4. pinned numbers go back to the owner's number bank, custom names to the name bank
  5. albums (collections) are emptied
  6. old catalog rows are hidden (is_active=0, kept because Obsidian cards still point at
     them); the new catalog (new_cards.py) is inserted, files live in static/nft/
  Obsidian (custom) cards are never touched.
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

import database as db
from new_cards import NEW_CARDS

APPLY = "--apply" in sys.argv
FORCE = "--force" in sys.argv

GEMS_PER_CARD = {"bronze": 25, "silver": 50, "gold": 150, "platinum": 600, "diamond": 3000}
DEFAULT_GEMS = 25
NEW_PREFIX = "../nft/"
MARKER = "migration_new_cards"

TRIGGER_DDL = """CREATE TRIGGER IF NOT EXISTS trg_card_retire_compensation
AFTER UPDATE OF is_active ON cards
WHEN NEW.is_active = 0 AND OLD.is_active = 1
BEGIN
    UPDATE users SET gems = gems + 25
    WHERE telegram_id IN (SELECT user_id FROM user_cards WHERE card_id = NEW.id);
END"""


def now():
    return datetime.now(timezone.utc).isoformat()


def backup():
    folder = db.DB_PATH.parent / "backups"
    folder.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    target = folder / f"peeppo_pre_nft_migration_{stamp}.db"
    src = sqlite3.connect(db.DB_PATH)
    dst = sqlite3.connect(target)
    src.backup(dst)
    dst.close()
    src.close()
    return target


def run(conn):
    rep = {}
    conn.row_factory = sqlite3.Row

    if conn.execute("SELECT 1 FROM action_counters WHERE action = ?", (MARKER,)).fetchone() and not FORCE:
        raise SystemExit("Migration already applied (marker in action_counters). Use --force only if you are sure.")

    rep["gems_total_before"] = conn.execute("SELECT COALESCE(SUM(gems),0) FROM users WHERE telegram_id > 0").fetchone()[0]
    rep["users_total"] = conn.execute("SELECT COUNT(*) FROM users WHERE telegram_id > 0").fetchone()[0]

    # ---- victims: every live, non-Obsidian card
    conn.execute("DROP TABLE IF EXISTS temp._victims")
    conn.execute("CREATE TEMP TABLE _victims (id INTEGER PRIMARY KEY, user_id INTEGER, rarity TEXT, override INTEGER, staked_at TEXT)")
    conn.execute(
        """
        INSERT INTO _victims (id, user_id, rarity, override, staked_at)
        SELECT uc.id, uc.user_id, c.rarity, uc.number_override, uc.staked_at
        FROM user_cards uc JOIN cards c ON c.id = uc.card_id
        WHERE uc.voided = 0 AND uc.custom_name IS NULL AND c.filename NOT LIKE ?
        """,
        (NEW_PREFIX + "%",),
    )
    rep["victim_cards"] = conn.execute("SELECT COUNT(*) FROM _victims").fetchone()[0]
    rep["obsidian_cards_left_alone"] = conn.execute(
        "SELECT COUNT(*) FROM user_cards WHERE voided = 0 AND custom_name IS NOT NULL"
    ).fetchone()[0]
    rep["victims_by_rarity"] = {
        r["rarity"]: r["n"] for r in conn.execute("SELECT rarity, COUNT(*) AS n FROM _victims GROUP BY rarity")
    }
    q = "SELECT COUNT(*) FROM user_cards WHERE id IN (SELECT id FROM _victims) AND "
    rep["victims_by_state"] = {
        "listed_for_sale": conn.execute(q + "listed_price IS NOT NULL").fetchone()[0],
        "swap_listed": conn.execute(q + "swap_listed = 1").fetchone()[0],
        "staked": conn.execute("SELECT COUNT(*) FROM _victims WHERE staked_at IS NOT NULL").fetchone()[0],
        "in_pvp_round": conn.execute(q + "pvp_round_id IS NOT NULL").fetchone()[0],
        "pinned_wall": conn.execute(q + "pinned_at IS NOT NULL").fetchone()[0],
        "transfer_pending": conn.execute(q + "transfer_pending = 1").fetchone()[0],
        "with_pinned_number": conn.execute("SELECT COUNT(*) FROM _victims WHERE override IS NOT NULL").fetchone()[0],
    }

    # ---- 1. staking income earned so far (full days only, same rule as settle_staking)
    stake_rows = conn.execute("SELECT id, user_id, rarity, staked_at FROM _victims WHERE staked_at IS NOT NULL").fetchall()
    stake_paid = {}
    nowdt = datetime.now(timezone.utc)
    for r in stake_rows:
        try:
            staked_at = datetime.fromisoformat(r["staked_at"])
        except Exception:
            continue
        full_days = int((nowdt - staked_at).total_seconds() // db.STAKE_PERIOD_SECONDS)
        full_days = min(full_days, db.MAX_STAKE_DAYS)
        if full_days >= 1:
            amount = full_days * db.STAKE_DAILY_RATES.get(r["rarity"] or "bronze", 5)
            stake_paid[r["user_id"]] = stake_paid.get(r["user_id"], 0) + amount
    for uid, amount in stake_paid.items():
        if uid <= 0:
            continue
        conn.execute(
            "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ?, "
            "staking_gems_earned = staking_gems_earned + ? WHERE telegram_id = ?",
            (amount, amount, amount, uid),
        )
    rep["staking_income_paid"] = sum(v for k, v in stake_paid.items() if k > 0)

    # ---- 2. refund amounts (computed before anything is touched)
    payout = {}
    for r in conn.execute("SELECT user_id, rarity, COUNT(*) AS n FROM _victims GROUP BY user_id, rarity"):
        if r["user_id"] <= 0:  # the PvP test bot never gets paid
            continue
        payout[r["user_id"]] = payout.get(r["user_id"], 0) + r["n"] * GEMS_PER_CARD.get(r["rarity"] or "bronze", DEFAULT_GEMS)
    rep["players_paid"] = len(payout)
    rep["gems_paid_for_cards"] = sum(payout.values())
    rep["top_payouts"] = sorted(payout.items(), key=lambda kv: -kv[1])[:10]

    # ---- 3. clear everything that points at those cards
    c = conn.execute("UPDATE market_offers SET status = 'cancelled' WHERE status = 'pending' AND user_card_id IN (SELECT id FROM _victims)")
    rep["market_offers_cancelled"] = c.rowcount
    c = conn.execute(
        "UPDATE swap_offers SET status = 'cancelled' WHERE status = 'pending' AND "
        "(user_card_id IN (SELECT id FROM _victims) OR id IN (SELECT swap_offer_id FROM swap_offer_cards WHERE user_card_id IN (SELECT id FROM _victims)))"
    )
    rep["swap_offers_cancelled"] = c.rowcount
    c = conn.execute("DELETE FROM blind_swap_listings WHERE user_card_id IN (SELECT id FROM _victims)")
    rep["blind_swap_listings_removed"] = c.rowcount

    c = conn.execute(
        "DELETE FROM pvp_entries WHERE user_card_id IN (SELECT id FROM _victims) "
        "AND round_id IN (SELECT id FROM pvp_rounds WHERE status = 'open')"
    )
    rep["open_pvp_entries_removed"] = c.rowcount
    # user_cards.pvp_round_id is a FK to pvp_rounds -- let go of it before any round is deleted
    conn.execute("UPDATE user_cards SET pvp_round_id = NULL WHERE id IN (SELECT id FROM _victims)")
    c = conn.execute(
        "DELETE FROM pvp_rounds WHERE status = 'open' AND id NOT IN (SELECT round_id FROM pvp_entries) "
        "AND id NOT IN (SELECT pvp_round_id FROM user_cards WHERE pvp_round_id IS NOT NULL)"
    )
    rep["empty_open_pvp_rounds_removed"] = c.rowcount

    undrawn = [r[0] for r in conn.execute(
        "SELECT id FROM number_giveaways WHERE drawn_at IS NULL AND user_card_id IN (SELECT id FROM _victims)"
    )]
    rep["undrawn_number_giveaways_cancelled"] = len(undrawn)
    for gid in undrawn:
        conn.execute("DELETE FROM number_giveaway_entries WHERE number_giveaway_id = ?", (gid,))
        conn.execute("DELETE FROM number_giveaways WHERE id = ?", (gid,))

    # ---- 4. numbers / names back to their owners' banks
    detached = 0
    for r in conn.execute("SELECT id FROM _victims WHERE override IS NOT NULL").fetchall():
        db._free_number(conn, r["id"])
        detached += 1
    c = conn.execute("UPDATE card_numbers SET user_card_id = NULL, updated_at = ? WHERE user_card_id IN (SELECT id FROM _victims)", (now(),))
    rep["pinned_numbers_returned_to_banks"] = detached
    rep["card_numbers_rows_detached_extra"] = c.rowcount
    c = conn.execute("UPDATE card_names SET user_card_id = NULL, updated_at = ? WHERE user_card_id IN (SELECT id FROM _victims)", (now(),))
    rep["names_returned_to_banks"] = c.rowcount

    # ---- 5. void the cards, pay the owners
    conn.execute(
        "UPDATE user_cards SET voided = 1, listed_price = NULL, listed_at = NULL, swap_listed = 0, "
        "staked_at = NULL, pvp_round_id = NULL, number_override = NULL, pinned_at = NULL, transfer_pending = 0 "
        "WHERE id IN (SELECT id FROM _victims)"
    )
    for uid, amount in payout.items():
        conn.execute("UPDATE users SET gems = gems + ? WHERE telegram_id = ?", (amount, uid))

    # ---- 6. albums emptied
    for table in ("user_collection_cards", "user_collection_completions", "collection_cards", "collections"):
        rep[f"{table}_deleted"] = conn.execute(f"DELETE FROM {table}").rowcount

    # ---- 7. catalog swap (the retire-compensation trigger must not fire for this)
    conn.execute("DROP TRIGGER IF EXISTS trg_card_retire_compensation")
    c = conn.execute("UPDATE cards SET is_active = 0 WHERE is_active = 1 AND filename NOT LIKE ?", (NEW_PREFIX + "%",))
    rep["old_catalog_cards_hidden"] = c.rowcount
    added = 0
    for key, name, rarity in NEW_CARDS:
        fn = f"{NEW_PREFIX}{key}.jpg"
        if conn.execute("SELECT 1 FROM cards WHERE filename = ?", (fn,)).fetchone():
            conn.execute("UPDATE cards SET name = ?, rarity = ?, is_active = 1 WHERE filename = ?", (name, rarity, fn))
            continue
        conn.execute(
            "INSERT INTO cards (filename, name, rarity, is_active, created_at) VALUES (?, ?, ?, 1, ?)",
            (fn, name, rarity, now()),
        )
        added += 1
    rep["new_cards_added"] = added
    rep["new_cards_by_rarity"] = {
        r["rarity"]: r["n"] for r in conn.execute(
            "SELECT rarity, COUNT(*) AS n FROM cards WHERE is_active = 1 AND filename LIKE ? GROUP BY rarity", (NEW_PREFIX + "%",)
        )
    }
    base = os.path.dirname(os.path.abspath(db.__file__))
    rep["new_cards_missing_file"] = [
        k for k, _, _ in NEW_CARDS if not os.path.exists(os.path.join(base, "static", "nft", k + ".jpg"))
    ]

    # ---- 8. marker + sanity checks
    conn.execute(
        "INSERT INTO action_counters (action, count) VALUES (?, 1) ON CONFLICT(action) DO UPDATE SET count = count + 1", (MARKER,)
    )
    left = conn.execute(
        "SELECT COUNT(*) FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
        "WHERE uc.voided = 0 AND uc.custom_name IS NULL AND c.filename NOT LIKE ?", (NEW_PREFIX + "%",)
    ).fetchone()[0]
    assert left == 0, f"{left} old non-Obsidian cards still alive"
    assert conn.execute("SELECT COUNT(*) FROM user_cards WHERE voided = 0 AND custom_name IS NULL").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM user_cards WHERE listed_price IS NOT NULL AND voided = 1").fetchone()[0] == 0
    rep["gems_total_after"] = conn.execute("SELECT COALESCE(SUM(gems),0) FROM users WHERE telegram_id > 0").fetchone()[0]
    rep["gems_created"] = rep["gems_total_after"] - rep["gems_total_before"]
    assert rep["gems_created"] == rep["gems_paid_for_cards"] + rep["staking_income_paid"], "gem accounting mismatch"
    rep["active_cards_now"] = conn.execute("SELECT COUNT(*) FROM cards WHERE is_active = 1").fetchone()[0]
    rep["payout_by_user"] = payout
    return rep


def main():
    print("MODE:", "APPLY" if APPLY else "DRY RUN (nothing will be saved)")
    if APPLY:
        print("backup ->", backup())
    conn = sqlite3.connect(db.DB_PATH, timeout=120)
    conn.isolation_level = None
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("BEGIN IMMEDIATE")
    try:
        rep = run(conn)
        if APPLY:
            conn.execute(TRIGGER_DDL)
            conn.execute("COMMIT")
        else:
            conn.execute("ROLLBACK")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    payout_by_user = rep.pop("payout_by_user")
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    if APPLY:
        out = db.DB_PATH.parent / f"migration_report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        out.write_text(json.dumps({"summary": rep, "payout_by_user": payout_by_user}, ensure_ascii=False, indent=2, default=str))
        print("report saved ->", out)
        print("DONE: migration applied.")
    else:
        print("DRY RUN finished -- nothing was changed. Run again with --apply to do it for real.")


if __name__ == "__main__":
    main()
