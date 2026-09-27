"""
Audit: every "cool" number (database.ALLOWED_AUCTION_NUMBERS -- vanity repdigits/round
numbers + every low number 1..LOW_NUMBER_SEED_UP_TO) must always be findable somewhere:
on a live card, sitting unattached in someone's number inventory, listed for resale, or
open on the auction board. Never just gone. This checks the live DB for exactly that,
and flags anything that looks stuck/inconsistent instead of just reporting normal state.

Run once from /root/peeppo on the server: python3 check_vanity_numbers.py
"""
import sqlite3
import database as db

conn = sqlite3.connect("peeppo.db")
conn.row_factory = sqlite3.Row

cool_numbers = sorted(db.ALLOWED_AUCTION_NUMBERS)

live_rows = conn.execute("""
    SELECT uc.id, uc.user_id, uc.number_override,
           COALESCE(uc.number_override,
             (SELECT COUNT(*) FROM user_cards uc2 WHERE uc2.obtained_at <= uc.obtained_at)) AS shown_number
    FROM user_cards uc WHERE uc.voided = 0
""").fetchall()
shown_by_number = {}
for r in live_rows:
    shown_by_number.setdefault(r["shown_number"], []).append(dict(r))

stuck_voided = conn.execute(
    "SELECT id, user_id, number_override FROM user_cards WHERE voided = 1 AND number_override IS NOT NULL"
).fetchall()

cn_rows = {r["number"]: dict(r) for r in conn.execute("SELECT * FROM card_numbers").fetchall()}

def username_of(uid):
    if uid is None:
        return None
    row = conn.execute("SELECT username, first_name FROM users WHERE telegram_id=?", (uid,)).fetchone()
    return (row["username"] or row["first_name"]) if row else f"?{uid}"

print(f"=== {len(cool_numbers)} cool numbers, current status ===\n")
problems = []
untracked = []
for n in cool_numbers:
    live = shown_by_number.get(n, [])
    cn = cn_rows.get(n)
    if len(live) > 1:
        problems.append(f"#{n}: DUPLICATE -- shown by {len(live)} different live cards at once: {live}")
    if cn and cn["user_card_id"] is not None:
        pin = conn.execute("SELECT voided FROM user_cards WHERE id=?", (cn["user_card_id"],)).fetchone()
        if pin is None or pin["voided"] == 1:
            problems.append(f"#{n}: card_numbers.user_card_id={cn['user_card_id']} points at a VOIDED/missing card -- should have been freed")
    if not live and not cn:
        untracked.append(n)
        continue
    if live:
        r = live[0]
        print(f"#{n}: on a live card (user_card_id={r['id']}, owner={username_of(r['user_id'])}, override={r['number_override']})")
    elif cn:
        if cn["status"] == "free":
            print(f"#{n}: free, open for auction")
        elif cn["status"] == "auction":
            print(f"#{n}: in auction, high bid {cn['highest_bid']} by {username_of(cn['highest_bidder_id'])}")
        elif cn["status"] == "owned":
            extra = f", LISTED for sale at {cn['list_price']}" if cn["list_price"] else " (unattached, not listed)"
            print(f"#{n}: owned by {username_of(cn['owner_id'])}{extra}")

print(f"\n=== {len(untracked)} not yet seeded/claimed by anyone (will auto-appear as 'free' next time the board loads) ===")
print(untracked)

print(f"\n=== {len(stuck_voided)} VOIDED cards still carrying a non-null number_override (should never happen) ===")
for r in stuck_voided:
    print(" ", dict(r))

print(f"\n=== {len(problems)} problems found ===")
for p in problems:
    print(" -", p)
if not problems and not stuck_voided:
    print("none -- every cool number is accounted for correctly.")
