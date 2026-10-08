"""Read-only diagnostic: lists every Obsidian (custom_name) card with its owner and state.
Usage on the server:  python3 diag_obsidian.py [@username_or_id]"""
import sqlite3, sys

db = sqlite3.connect("/root/peeppo/peeppo.db")
db.row_factory = sqlite3.Row
who = sys.argv[1].lstrip("@") if len(sys.argv) > 1 else None
rows = db.execute(
    """
    SELECT uc.id AS uc_id, uc.user_id, u.username, uc.voided, uc.custom_name, uc.custom_rarity,
           uc.number_override, uc.listed_price, uc.swap_listed, uc.staked_at, uc.pvp_round_id,
           uc.obtained_at, c.name AS card_name
    FROM user_cards uc
    JOIN cards c ON c.id = uc.card_id
    LEFT JOIN users u ON u.telegram_id = uc.user_id
    WHERE uc.custom_name IS NOT NULL OR uc.custom_rarity IS NOT NULL
    ORDER BY uc.id DESC
    """
).fetchall()
for r in rows:
    if who and who not in (str(r["user_id"]), (r["username"] or "")):
        continue
    print(dict(r))
print(f"rows: {len(rows)}")
