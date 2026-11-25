"""One-off: move everything from the admin's game account to @zzabeyda.

  python3 transfer_account.py --dry-run   # only show what would change
  python3 transfer_account.py             # backup DB, then transfer in one transaction

Moves: live cards (user_cards, not voided), owned numbers (card_numbers), owned names
(card_names), blind-swap listings, collection placements/completions (where mom has no
duplicate), villa free credits, gems (added to mom, zeroed on admin), purchased rank tier.
History (game rounds, payments, offers, pvp, villa_purchases, referrals) stays as is.
Nothing is deleted.
"""
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB = Path("/root/peeppo/peeppo.db")
SRC = 7308147004
DST_USERNAME = "zzabeyda"
DRY = "--dry-run" in sys.argv

c = sqlite3.connect(str(DB), timeout=30)
c.row_factory = sqlite3.Row
src_row = c.execute("SELECT telegram_id FROM users WHERE lower(username)='rzabeyda'").fetchone()
if not src_row or src_row[0] != SRC:
    sys.exit(f"@rzabeyda id mismatch: {src_row and src_row[0]}")
dst_row = c.execute("SELECT telegram_id FROM users WHERE lower(username)=?", (DST_USERNAME,)).fetchone()
if not dst_row:
    sys.exit("mom not found")
DST = dst_row[0]


def snap(uid):
    u = c.execute("SELECT gems, gems_earned, purchased_rank_tier, selected_rank_tier FROM users WHERE telegram_id=?", (uid,)).fetchone()
    one = lambda s: c.execute(s, (uid,)).fetchone()[0]
    return {
        "gems": u["gems"],
        "gems_earned": u["gems_earned"],
        "rank_tier": u["purchased_rank_tier"],
        "cards": one("SELECT COUNT(*) FROM user_cards WHERE user_id=? AND voided=0"),
        "villas": one("SELECT COUNT(*) FROM user_cards uc JOIN cards ca ON ca.id=uc.card_id WHERE uc.user_id=? AND uc.voided=0 AND ca.villa_number IS NOT NULL"),
        "numbers": one("SELECT COUNT(*) FROM card_numbers WHERE owner_id=?"),
        "names": one("SELECT COUNT(*) FROM card_names WHERE owner_id=?"),
        "blind_swap": one("SELECT COUNT(*) FROM blind_swap_listings WHERE user_id=?"),
        "coll_cards": one("SELECT COUNT(*) FROM user_collection_cards WHERE user_id=?"),
        "coll_done": one("SELECT COUNT(*) FROM user_collection_completions WHERE user_id=?"),
        "villa_credits": (c.execute("SELECT credits FROM villa_free_credits WHERE user_id=?", (uid,)).fetchone() or [0])[0],
    }


def show(title):
    a, m = snap(SRC), snap(DST)
    print(f"\n== {title} ==   admin {SRC}  |  mom {DST}")
    for k in a:
        print(f"  {k:14} {a[k]!s:>12}  |  {m[k]!s:>12}")


show("BEFORE")

# Safety checks: nothing of the admin's mid-flight
busy = c.execute(
    "SELECT COUNT(*) FROM user_cards uc JOIN pvp_rounds r ON r.id=uc.pvp_round_id "
    "WHERE uc.user_id=? AND r.status!='resolved'", (SRC,)).fetchone()[0]
busy += c.execute("SELECT COUNT(*) FROM card_numbers WHERE highest_bidder_id=? AND status='auction'", (SRC,)).fetchone()[0]
busy += c.execute("SELECT COUNT(*) FROM card_names WHERE highest_bidder_id=? AND status='auction'", (SRC,)).fetchone()[0]
busy += c.execute("SELECT COUNT(*) FROM number_giveaways WHERE admin_id=? AND drawn_at IS NULL", (SRC,)).fetchone()[0]
busy += c.execute("SELECT COUNT(*) FROM user_cards WHERE user_id=? AND voided=0 AND transfer_pending=1", (SRC,)).fetchone()[0]
if busy:
    sys.exit(f"STOP: {busy} active pvp/auction/giveaway/pending items on admin -- finish them first")

if DRY:
    print("\n--dry-run: nothing changed")
    sys.exit(0)

backup = DB.with_name(f"peeppo.db.before_transfer_{datetime.now():%Y%m%d_%H%M%S}")
src_bk = sqlite3.connect(str(DB), timeout=30)
dst_bk = sqlite3.connect(str(backup))
src_bk.backup(dst_bk)  # consistent copy even with WAL
dst_bk.close(); src_bk.close()
print(f"\nbackup: {backup}")

with c:  # one transaction
    c.execute("BEGIN IMMEDIATE")
    g = c.execute("SELECT gems FROM users WHERE telegram_id=?", (SRC,)).fetchone()[0]
    c.execute("UPDATE users SET gems = gems + ? WHERE telegram_id=?", (g, DST))
    c.execute("UPDATE users SET gems = 0 WHERE telegram_id=?", (SRC,))
    c.execute("UPDATE users SET purchased_rank_tier = MAX(purchased_rank_tier, "
              "(SELECT purchased_rank_tier FROM users WHERE telegram_id=?)) WHERE telegram_id=?", (SRC, DST))
    c.execute("UPDATE user_cards SET user_id=? WHERE user_id=? AND voided=0", (DST, SRC))
    c.execute("UPDATE card_numbers SET owner_id=? WHERE owner_id=?", (DST, SRC))
    c.execute("UPDATE card_names SET owner_id=? WHERE owner_id=?", (DST, SRC))
    c.execute("UPDATE OR IGNORE blind_swap_listings SET user_id=? WHERE user_id=?", (DST, SRC))
    c.execute("UPDATE OR IGNORE user_collection_cards SET user_id=? WHERE user_id=?", (DST, SRC))
    c.execute("UPDATE OR IGNORE user_collection_completions SET user_id=? WHERE user_id=?", (DST, SRC))
    vc = (c.execute("SELECT credits FROM villa_free_credits WHERE user_id=?", (SRC,)).fetchone() or [0])[0]
    if vc:
        c.execute("INSERT INTO villa_free_credits (user_id, credits) VALUES (?, ?) "
                  "ON CONFLICT(user_id) DO UPDATE SET credits = credits + excluded.credits", (DST, vc))
        c.execute("UPDATE villa_free_credits SET credits=0 WHERE user_id=?", (SRC,))

show("AFTER")
