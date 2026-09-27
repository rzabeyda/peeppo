"""
One-off diagnostic: pin down exactly what user_cards.id=911 (the card from the most
recent market_offers row, buyer = admin) looks like right now -- name, rarity,
current live-computed drop_number, and confirmed owner -- so we can tell the user
precisely how to find it in their Profile grid instead of guessing.

Run once from /root/peeppo on the server: python3 check_card_911.py
"""
import sqlite3

conn = sqlite3.connect("peeppo.db")
conn.row_factory = sqlite3.Row

row = conn.execute(
    """
    SELECT uc.id, uc.user_id, uc.voided, uc.obtained_at, uc.number_override,
           uc.listed_price, uc.swap_listed, uc.staked_at, uc.pvp_round_id, uc.pinned_at,
           COALESCE(uc.number_override,
               (SELECT COUNT(*) FROM user_cards uc2 WHERE uc2.obtained_at <= uc.obtained_at)) AS drop_number,
           c.name, c.rarity, c.filename
    FROM user_cards uc JOIN cards c ON c.id = uc.card_id
    WHERE uc.id = 911
    """
).fetchone()
print(dict(row) if row else "user_cards.id=911 not found")

conn.close()
