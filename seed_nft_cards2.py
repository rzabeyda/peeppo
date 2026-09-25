"""One-off script: registers 12 more new NFT card images (already copied into
static/cards/) in the catalog with the correct rarity. Run once from /root/peeppo
on the VPS (same folder as database.py) — idempotent, skips any filename that's
already registered, so it's safe to re-run if it gets interrupted.
"""
import database as db

NEW_CARDS = [
    # (filename, display name, rarity)
    ("i_believe.jpg", "I Want To Believe", "bronze"),
    ("soda.jpg", "Soda", "bronze"),
    ("spinner.jpg", "Spinner", "bronze"),
    ("yo_yo.jpg", "Yo-Yo", "bronze"),
    ("tarhun.jpg", "Tarhun", "bronze"),
    ("soap.jpg", "Soap 72%", "bronze"),
    ("dust_2.jpg", "Dust 2", "bronze"),
    ("sguwonka.jpg", "Sguschonka", "bronze"),
    ("knife_1.5.jpg", "Knife 1.5", "silver"),
    ("yandex_taxi.jpg", "Yandex Taxi", "gold"),
    ("mp5.jpg", "MP5", "gold"),
    ("awp.jpg", "AWP", "gold"),
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
