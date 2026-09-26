"""
One-off: registers scoprion.jpg (the Scorpion character art -- separate file, sitting
unregistered in static/cards/ since well before this session) as its own card, since
mortal_kombat.jpg (its old filename co-tenant / the card id that used to be named
"Scorpion") got repurposed today for the new arcade-cabinet artwork. Idempotent.

Usage (on the server):
    python3 add_scorpion_card.py
"""

import database as db

FILENAME = "scoprion.jpg"
NAME = "Scorpion"
RARITY = "bronze"

db.init_db()

with db.get_conn() as conn:
    row = conn.execute("SELECT id, name, rarity FROM cards WHERE filename = ?", (FILENAME,)).fetchone()

if row:
    print(f"already registered: #{row['id']} {FILENAME} -> {row['name']!r} ({row['rarity']})")
else:
    card_id = db.add_card_to_catalog(FILENAME, NAME, RARITY)
    print(f"added #{card_id}: {FILENAME} -> {NAME!r} ({RARITY})")
