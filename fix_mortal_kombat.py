"""
One-off: mortal_kombat.jpg was already registered under an old card (id=96) named
"Scorpion" at bronze rarity from a previous, unrelated catalog entry that happened to
reuse this exact filename -- when the new arcade-cabinet image was dropped in under
the same name, add_nft_arcade_batch.py correctly skipped it as "already registered"
(filename match), but that left the new artwork showing under the old name/rarity.
This just relabels that same row to match the rest of the batch. Idempotent.

Usage (on the server):
    python3 fix_mortal_kombat.py
"""

import database as db

with db.get_conn() as conn:
    row = conn.execute("SELECT id, name, rarity FROM cards WHERE filename = ?", ("mortal_kombat.jpg",)).fetchone()
    if row is None:
        print("mortal_kombat.jpg not found in catalog")
    elif row["name"] == "Mortal Kombat" and row["rarity"] == "gold":
        print(f"#{row['id']}: already correct (Mortal Kombat / gold)")
    else:
        conn.execute("UPDATE cards SET name = 'Mortal Kombat', rarity = 'gold' WHERE id = ?", (row["id"],))
        print(f"#{row['id']}: {row['name']!r} ({row['rarity']}) -> 'Mortal Kombat' (gold)")
