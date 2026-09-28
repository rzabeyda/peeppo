import sqlite3
conn = sqlite3.connect("peeppo.db")
conn.row_factory = sqlite3.Row

UID = 5392127313

print("=== user row ===")
u = conn.execute("SELECT telegram_id, username, first_name, gems, created_at FROM users WHERE telegram_id=?", (UID,)).fetchone()
print(dict(u) if u else "NOT FOUND")

print()
print("=== all his diamond user_cards (voided or not) ===")
rows = conn.execute(
    "SELECT uc.id, uc.obtained_at, uc.voided, uc.listed_price, uc.swap_listed, uc.staked_at, uc.pvp_round_id "
    "FROM user_cards uc JOIN cards c ON c.id=uc.card_id "
    "WHERE uc.user_id=? AND c.rarity='diamond' ORDER BY uc.obtained_at",
    (UID,)
).fetchall()
print("total diamond cards ever (incl voided):", len(rows))
for r in rows:
    print(dict(r))

print()
print("=== his crypto_withdrawals (all statuses) ===")
for r in conn.execute("SELECT * FROM crypto_withdrawals WHERE user_id=? ORDER BY id", (UID,)).fetchall():
    print(dict(r))

print()
print("=== crypto_withdrawal_cards linked to his withdrawals ===")
for r in conn.execute(
    "SELECT cwc.withdrawal_id, cwc.user_card_id FROM crypto_withdrawal_cards cwc "
    "JOIN crypto_withdrawals cw ON cw.id=cwc.withdrawal_id WHERE cw.user_id=? ORDER BY cwc.withdrawal_id",
    (UID,)
).fetchall():
    print(dict(r))

print()
print("=== duplicate user_card_id across DIFFERENT withdrawals (should be empty) ===")
dupe = conn.execute(
    "SELECT user_card_id, COUNT(*) c FROM crypto_withdrawal_cards GROUP BY user_card_id HAVING c > 1"
).fetchall()
print(dupe if dupe else "none")

print()
print("=== withdrawal #3 request time vs now, to check deploy timing ===")
r3 = conn.execute("SELECT * FROM crypto_withdrawals WHERE id=3").fetchone()
print(dict(r3) if r3 else "no #3")
