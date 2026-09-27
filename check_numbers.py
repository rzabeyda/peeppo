import sqlite3

conn = sqlite3.connect("peeppo.db")
conn.row_factory = sqlite3.Row

NUMBERS = [1, 666]

for n in NUMBERS:
    print(f"=== number {n} ===")
    row = conn.execute("SELECT * FROM card_numbers WHERE number = ?", (n,)).fetchone()
    if row is None:
        print("  not tracked in card_numbers at all (never seeded/claimed) -- meaning it's still just")
        print("  the natural Nth-farmed-card number, nobody has extracted/bought/bid on it yet.")
        continue
    d = dict(row)
    print(" ", d)
    if d["owner_id"]:
        owner = conn.execute(
            "SELECT username, first_name FROM users WHERE telegram_id = ?", (d["owner_id"],)
        ).fetchone()
        if owner:
            print(f"  owner: {owner['username'] or owner['first_name']} (id {d['owner_id']})")
    if d["highest_bidder_id"]:
        bidder = conn.execute(
            "SELECT username, first_name FROM users WHERE telegram_id = ?", (d["highest_bidder_id"],)
        ).fetchone()
        if bidder:
            print(f"  highest bidder: {bidder['username'] or bidder['first_name']} (id {d['highest_bidder_id']}), bid={d['highest_bid']}")
    if d["user_card_id"]:
        uc = conn.execute(
            "SELECT uc.user_id, u.username, u.first_name FROM user_cards uc JOIN users u ON u.telegram_id = uc.user_id WHERE uc.id = ?",
            (d["user_card_id"],),
        ).fetchone()
        if uc:
            print(f"  currently pinned on a card owned by: {uc['username'] or uc['first_name']} (id {uc['user_id']})")
    print()
