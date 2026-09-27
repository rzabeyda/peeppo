"""
One-off diagnostic: user says they bought a card showing #101 on the Market but it's
not showing up in their Profile. This checks the live DB directly instead of guessing:
- every user_cards row that currently DISPLAYS number 101 (natural or overridden),
  regardless of who owns it or whether it's voided
- the admin's own most recent 10 obtained cards (assuming the admin account is the one
  testing this)
- the card_numbers row for 101, if any (in case 101 is tracked there too)

Run once from /root/peeppo on the server: python3 check_number_101.py
"""
import os
import sqlite3

from dotenv import load_dotenv

load_dotenv()
ADMIN_ID = os.environ.get("ADMIN_ID")

conn = sqlite3.connect("peeppo.db")
conn.row_factory = sqlite3.Row

print("=== user_cards currently showing number 101 (natural or overridden) ===")
rows = conn.execute(
    """
    SELECT uc.id, uc.user_id, uc.voided, uc.obtained_at, uc.number_override, uc.listed_price,
           uc.swap_listed, uc.staked_at, uc.pvp_round_id,
           COALESCE(uc.number_override,
               (SELECT COUNT(*) FROM user_cards uc2 WHERE uc2.obtained_at <= uc.obtained_at)) AS drop_number,
           c.name
    FROM user_cards uc JOIN cards c ON c.id = uc.card_id
    """
).fetchall()
for r in rows:
    if r["drop_number"] == 101:
        print(dict(r))

print()
print("=== card_numbers row for number 101 ===")
row = conn.execute("SELECT * FROM card_numbers WHERE number = 101").fetchone()
print(dict(row) if row else "none")

if ADMIN_ID:
    print()
    print(f"=== admin's ({ADMIN_ID}) 10 most recently obtained cards ===")
    rows = conn.execute(
        """
        SELECT uc.id, uc.voided, uc.obtained_at, uc.number_override, uc.listed_price,
               COALESCE(uc.number_override,
                   (SELECT COUNT(*) FROM user_cards uc2 WHERE uc2.obtained_at <= uc.obtained_at)) AS drop_number,
               c.name
        FROM user_cards uc JOIN cards c ON c.id = uc.card_id
        WHERE uc.user_id = ?
        ORDER BY uc.obtained_at DESC LIMIT 10
        """,
        (int(ADMIN_ID),),
    ).fetchall()
    for r in rows:
        print(dict(r))

print()
print("=== most recent 5 market_offers rows (any status) ===")
rows = conn.execute(
    "SELECT * FROM market_offers ORDER BY id DESC LIMIT 5"
).fetchall()
for r in rows:
    print(dict(r))

conn.close()
