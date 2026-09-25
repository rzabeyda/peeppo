"""One-off script: registers the 20 new NFT card images (already copied into
static/cards/) in the catalog with the correct rarity. Run once from /root/peeppo
on the VPS (same folder as database.py) — idempotent, skips any filename that's
already registered, so it's safe to re-run if it gets interrupted.
"""
import database as db

NEW_CARDS = [
    # (filename, display name, rarity)
    ("soda_cup.jpg", "Soda Cup", "bronze"),
    ("earphones.jpg", "Earphones", "bronze"),
    ("crumpled_bill.jpg", "Crumpled Bill", "bronze"),
    ("crab_snacks.jpg", "Crab Snacks", "bronze"),
    ("oversize_hoodie.jpg", "Oversize Hoodie", "bronze"),
    ("fake_sneakers.jpg", "Fake Sneakers", "bronze"),
    ("meme_shopper.jpg", "Meme Shopper", "bronze"),
    ("nokia_charger.jpg", "Nokia Charger", "bronze"),
    ("heavy_powerbank.jpg", "Heavy Powerbank", "bronze"),
    ("anime_sweatshirt.jpg", "Anime Sweatshirt", "bronze"),
    ("streamer_mic.jpg", "Streamer Mic", "silver"),
    ("rgb_keyboard.jpg", "RGB Keyboard", "silver"),
    ("honeycomb_mouse.jpg", "Honeycomb Mouse", "silver"),
    ("gaming_chair.jpg", "Gaming Chair", "silver"),
    ("hyped_sneakers.jpg", "Hyped Sneakers", "silver"),
    ("cyber_rig.jpg", "Cyber Rig", "gold"),
    ("pro_longboard.jpg", "Pro Longboard", "gold"),
    ("pro_buds.jpg", "Pro Buds", "gold"),
    ("ultimate_gpu.jpg", "Ultimate GPU", "gold"),
    ("water_pc.jpg", "Water PC", "gold"),
]

with db.get_conn() as conn:
    existing = {row["filename"] for row in conn.execute("SELECT filename FROM cards")}

added, skipped = 0, 0
for filename, name, rarity in NEW_CARDS:
    if filename in existing:
        print(f"skip (already in catalog): {filename}")
        skipped += 1
        continue
    card_id = db.add_card_to_catalog(filename, name, rarity)
    print(f"added #{card_id}: {filename} -> {name} ({rarity})")
    added += 1

print(f"\nDone. Added {added}, skipped {skipped} (already existed).")
