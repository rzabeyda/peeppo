import sqlite3
from datetime import datetime, timezone

DB = "/root/peeppo/peeppo.db"
UID = 7785933639

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA foreign_keys=ON")

now = datetime.now(timezone.utc).isoformat()

card_ids = [r["id"] for r in conn.execute("SELECT id FROM user_cards WHERE user_id = ?", (UID,)).fetchall()]
print(f"user_cards to wipe: {len(card_ids)}")
ph = ",".join("?" * len(card_ids)) if card_ids else "NULL"

try:
    conn.execute("BEGIN")

    # swap_offers that touch him (as buyer, or the card being swapped is his) --
    # purge ALL swap_offer_cards children of those offers first, plus any
    # swap_offer_cards row anywhere that references one of his card ids
    # (e.g. his card offered as payment inside someone else's swap offer).
    offer_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM swap_offers WHERE buyer_id=? OR user_card_id IN (SELECT id FROM user_cards WHERE user_id=?)",
        (UID, UID),
    ).fetchall()]
    for oid in offer_ids:
        conn.execute("DELETE FROM swap_offer_cards WHERE swap_offer_id=?", (oid,))
    if card_ids:
        conn.execute(f"DELETE FROM swap_offer_cards WHERE user_card_id IN ({ph})", card_ids)
    if offer_ids:
        conn.execute(f"DELETE FROM swap_offers WHERE id IN ({','.join('?'*len(offer_ids))})", offer_ids)

    conn.execute(
        "DELETE FROM market_offers WHERE buyer_id=? OR user_card_id IN (SELECT id FROM user_cards WHERE user_id=?)",
        (UID, UID),
    )

    # pvp: clear winner_id on rounds he won (keep the round + other entrants intact),
    # then drop every entry that's either his OR sits on a card id that's now his
    # (a card someone else entered with, that later changed hands to him by trade).
    conn.execute("UPDATE pvp_rounds SET winner_id = NULL WHERE winner_id = ?", (UID,))
    if card_ids:
        conn.execute(
            f"DELETE FROM pvp_entries WHERE user_id=? OR user_card_id IN ({ph})",
            (UID, *card_ids),
        )
    else:
        conn.execute("DELETE FROM pvp_entries WHERE user_id=?", (UID,))

    conn.execute("UPDATE gem_drops SET claimed_by = NULL, claimed_at = NULL WHERE claimed_by = ?", (UID,))

    conn.execute("DELETE FROM giveaway_entries WHERE user_id = ?", (UID,))
    conn.execute("DELETE FROM number_giveaway_entries WHERE user_id = ?", (UID,))
    if card_ids:
        ng_ids = [r["id"] for r in conn.execute(
            f"SELECT id FROM number_giveaways WHERE user_card_id IN ({ph})", card_ids
        ).fetchall()]
        for gid in ng_ids:
            conn.execute("DELETE FROM number_giveaway_entries WHERE number_giveaway_id=?", (gid,))
        if ng_ids:
            conn.execute(f"DELETE FROM number_giveaways WHERE id IN ({','.join('?'*len(ng_ids))})", ng_ids)
    conn.execute("DELETE FROM card_batch_giveaway_entries WHERE user_id = ?", (UID,))

    conn.execute("DELETE FROM aviator_rounds WHERE user_id = ?", (UID,))
    conn.execute("DELETE FROM mines_rounds WHERE user_id = ?", (UID,))
    conn.execute("DELETE FROM redblack_rounds WHERE user_id = ?", (UID,))
    conn.execute("DELETE FROM plinko_rounds WHERE user_id = ?", (UID,))
    conn.execute("DELETE FROM poker_rounds WHERE user_id = ?", (UID,))

    wd_ids = [r["id"] for r in conn.execute("SELECT id FROM crypto_withdrawals WHERE user_id=?", (UID,)).fetchall()]
    for wid in wd_ids:
        conn.execute("DELETE FROM crypto_withdrawal_cards WHERE withdrawal_id=?", (wid,))
    if card_ids:
        conn.execute(f"DELETE FROM crypto_withdrawal_cards WHERE user_card_id IN ({ph})", card_ids)
    conn.execute("DELETE FROM crypto_withdrawals WHERE user_id = ?", (UID,))

    conn.execute(
        "UPDATE card_numbers SET status='free', owner_id=NULL, user_card_id=NULL, "
        "highest_bid=NULL, highest_bidder_id=NULL, list_price=NULL, updated_at=? WHERE owner_id = ?",
        (now, UID),
    )
    conn.execute(
        "UPDATE card_numbers SET highest_bid=NULL, highest_bidder_id=NULL, updated_at=? WHERE highest_bidder_id = ?",
        (now, UID),
    )
    if card_ids:
        conn.execute(f"UPDATE card_numbers SET user_card_id=NULL, updated_at=? WHERE user_card_id IN ({ph})", (now, *card_ids))
    conn.execute(
        "UPDATE card_names SET owner_id=NULL, user_card_id=NULL, list_price=NULL, updated_at=? WHERE owner_id = ?",
        (now, UID),
    )
    conn.execute(
        "UPDATE card_names SET highest_bid=NULL, highest_bidder_id=NULL, updated_at=? WHERE highest_bidder_id = ?",
        (now, UID),
    )
    if card_ids:
        conn.execute(f"UPDATE card_names SET user_card_id=NULL, updated_at=? WHERE user_card_id IN ({ph})", (now, *card_ids))

    conn.execute("DELETE FROM user_collection_cards WHERE user_id = ?", (UID,))
    conn.execute("DELETE FROM user_collection_completions WHERE user_id = ?", (UID,))
    conn.execute("DELETE FROM stars_payments WHERE user_id = ?", (UID,))
    if card_ids:
        conn.execute(f"DELETE FROM blind_swap_listings WHERE user_id=? OR user_card_id IN ({ph})", (UID, *card_ids))
    else:
        conn.execute("DELETE FROM blind_swap_listings WHERE user_id=?", (UID,))

    conn.execute("UPDATE users SET ref_by = NULL WHERE ref_by = ?", (UID,))

    conn.execute("DELETE FROM user_cards WHERE user_id = ?", (UID,))
    conn.execute("DELETE FROM users WHERE telegram_id = ?", (UID,))

    conn.commit()
    print("--- WIPE COMMITTED ---")
except Exception as e:
    conn.rollback()
    print(f"--- ROLLED BACK, ERROR: {e!r} ---")
    raise

after = conn.execute("SELECT COUNT(*) FROM users WHERE telegram_id=?", (UID,)).fetchone()[0]
after_cards = conn.execute("SELECT COUNT(*) FROM user_cards WHERE user_id=?", (UID,)).fetchone()[0]
print(f"users row left: {after}")
print(f"user_cards left: {after_cards}")
conn.close()
