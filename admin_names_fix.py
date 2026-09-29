import sqlite3
from datetime import datetime, timezone

conn = sqlite3.connect("/root/peeppo/peeppo.db")
conn.row_factory = sqlite3.Row

def now():
    return datetime.now(timezone.utc).isoformat()

print("=== reserved names status (rzabeyda/zabeyda/zzabeyda) ===")
rows = conn.execute("""
    SELECT cn.name, cn.status, cn.owner_id, cn.user_card_id, cn.list_price,
           uc.custom_name, uc.card_id
    FROM card_names cn
    LEFT JOIN user_cards uc ON uc.id = cn.user_card_id
    WHERE cn.name IN ('rzabeyda','zabeyda','zzabeyda')
""").fetchall()
if not rows:
    print("(none of these three currently exist in card_names)")
for r in rows:
    print(dict(r))

print()
print("=== granting 'peeppo' to admin (rzabeyda) ===")
admin = conn.execute("SELECT telegram_id FROM users WHERE username = 'rzabeyda' COLLATE NOCASE").fetchone()
if not admin:
    print("NOT FOUND -- no users row with username 'rzabeyda'")
else:
    existing_peeppo = conn.execute("SELECT * FROM card_names WHERE name = 'peeppo'").fetchone()
    if existing_peeppo:
        print("'peeppo' already exists, NOT touched:", dict(existing_peeppo))
    else:
        ts = now()
        conn.execute(
            "INSERT INTO card_names (name, status, owner_id, user_card_id, highest_bid, "
            "highest_bidder_id, bid_expires_at, list_price, created_at, updated_at) "
            "VALUES ('peeppo', 'owned', ?, NULL, NULL, NULL, NULL, NULL, ?, ?)",
            (admin["telegram_id"], ts, ts),
        )
        conn.commit()
        print(f"Granted 'peeppo' to telegram_id={admin['telegram_id']}")
