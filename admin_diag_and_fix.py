import sqlite3
from datetime import datetime, timezone

conn = sqlite3.connect("/root/peeppo/peeppo.db")
conn.row_factory = sqlite3.Row

def now():
    return datetime.now(timezone.utc).isoformat()

print("=== @kexsw referral count ===")
u = conn.execute("SELECT telegram_id, username, first_name FROM users WHERE username = 'kexsw' COLLATE NOCASE").fetchone()
if not u:
    print("no user with username 'kexsw' found")
else:
    n = conn.execute("SELECT COUNT(*) AS n FROM users WHERE ref_by = ?", (u["telegram_id"],)).fetchone()["n"]
    print(f"{u['username']} (id {u['telegram_id']}): {n} referrals")

print()
print("=== 'pepe' name status (card_names) ===")
row = conn.execute("""
    SELECT cn.name, cn.status, cn.owner_id, cn.user_card_id, cn.list_price,
           uc.custom_name, uc.card_id
    FROM card_names cn
    LEFT JOIN user_cards uc ON uc.id = cn.user_card_id
    WHERE cn.name = 'pepe'
""").fetchone()
print(dict(row) if row else "no 'pepe' row in card_names")

print()
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

print()
print("=== my PvP stats (cards, net) ===")
if admin:
    aid = admin["telegram_id"]
    won = conn.execute("""
        SELECT COUNT(*) AS n FROM pvp_entries pe JOIN pvp_rounds pr ON pr.id = pe.round_id
        WHERE pr.status='resolved' AND pr.winner_id = ? AND pe.user_id != ?
    """, (aid, aid)).fetchone()["n"]
    lost = conn.execute("""
        SELECT COUNT(*) AS n FROM pvp_entries pe JOIN pvp_rounds pr ON pr.id = pe.round_id
        WHERE pr.status='resolved' AND pr.winner_id IS NOT NULL AND pr.winner_id != ? AND pe.user_id = ?
    """, (aid, aid)).fetchone()["n"]
    rounds_won = conn.execute("SELECT COUNT(*) AS n FROM pvp_rounds WHERE status='resolved' AND winner_id = ?", (aid,)).fetchone()["n"]
    print(f"rounds won: {rounds_won}")
    print(f"cards captured (won): {won}")
    print(f"cards lost: {lost}")
    print(f"net: {won - lost} ({'в плюсе' if won - lost > 0 else ('в минусе' if won - lost < 0 else 'ровно')})")

    print()
    print("=== my PvP stats (diamond only, net) ===")
    won_d = conn.execute("""
        SELECT COUNT(*) AS n FROM pvp_entries pe
        JOIN pvp_rounds pr ON pr.id = pe.round_id
        JOIN user_cards uc ON uc.id = pe.user_card_id
        JOIN cards c ON c.id = uc.card_id
        WHERE pr.status='resolved' AND pr.winner_id = ? AND pe.user_id != ? AND c.rarity='diamond'
    """, (aid, aid)).fetchone()["n"]
    lost_d = conn.execute("""
        SELECT COUNT(*) AS n FROM pvp_entries pe
        JOIN pvp_rounds pr ON pr.id = pe.round_id
        JOIN user_cards uc ON uc.id = pe.user_card_id
        JOIN cards c ON c.id = uc.card_id
        WHERE pr.status='resolved' AND pr.winner_id IS NOT NULL AND pr.winner_id != ? AND pe.user_id = ? AND c.rarity='diamond'
    """, (aid, aid)).fetchone()["n"]
    print(f"diamond cards captured (won): {won_d}")
    print(f"diamond cards lost: {lost_d}")
    print(f"diamond net: {won_d - lost_d} ({'в плюсе' if won_d - lost_d > 0 else ('в минусе' if won_d - lost_d < 0 else 'ровно')})")
