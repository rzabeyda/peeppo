"""
One-off: registers the new arcade-cabinet NFT batch (gold) plus the diamond/platinum
iPhones and the billiard table. Idempotent -- safe to run more than once,
already-registered filenames are skipped.

Usage (from the server, after scp-ing the images into static/cards/):
    python3 add_nft_arcade_batch.py
"""

import database as db

# (filename, display name, rarity)
CARDS = [
    ("tekken.jpg", "Tekken", "gold"),
    ("mortal_kombat.jpg", "Mortal Kombat", "gold"),
    ("street_fighter.jpg", "Street Fighter", "gold"),
    ("cadillacs_dino.jpg", "Cadillacs Dino", "gold"),
    ("metal_slug.jpg", "Metal Slug", "gold"),
    ("iphone_diamond.jpg", "iPhone Diamond", "diamond"),
    ("iphone_platinum.jpg", "iPhone Platinum", "platinum"),
    ("biliard.jpg", "Billiard Table", "platinum"),
]

db.init_db()

with db.get_conn() as conn:
    known = {row["filename"] for row in conn.execute("SELECT filename FROM cards")}

added = 0
for filename, name, rarity in CARDS:
    if filename in known:
        print(f"skip (already registered): {filename}")
        continue
    card_id = db.add_card_to_catalog(filename, name, rarity)
    print(f"added #{card_id}: {filename} -> {name} ({rarity})")
    added += 1

print(f"done — {added} new card(s) registered (of {len(CARDS)} in batch)")
