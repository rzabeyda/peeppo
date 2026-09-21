"""
One-off: registers a new batch of luxury/diamond-rarity cards in the catalog.
Name is always exactly the humanized filename (add_card.py's name_for()).
Idempotent — safe to run more than once.

Usage (from the server, after scp-ing the images into static/cards/):
    python3 add_diamond_cards2.py
"""

import database as db
from add_card import name_for

FILENAMES = [
    "hope_diamond.jpg",
    "codex_leicester.jpg",
    "dubai_royale.jpg",
    "pinner_vase.jpg",
    "coutts_silk.jpg",
    "codex_sassoon.jpg",
    "jpmorgan_reserve.jpg",
    "marengo_sword.jpg",
    "american_platinum.jpg",
    "stratus_visa.jpg",
]

with db.get_conn() as conn:
    known = {row["filename"]: row["id"] for row in conn.execute("SELECT id, filename FROM cards")}

added, renamed = 0, 0
for filename in FILENAMES:
    name = name_for(filename)
    if filename in known:
        with db.get_conn() as conn:
            conn.execute("UPDATE cards SET name = ?, rarity = 'diamond' WHERE id = ?", (name, known[filename]))
        print(f"renamed #{known[filename]}: {filename} -> {name} (diamond)")
        renamed += 1
    else:
        card_id = db.add_card_to_catalog(filename, name)
        with db.get_conn() as conn:
            conn.execute("UPDATE cards SET rarity = 'diamond' WHERE id = ?", (card_id,))
        print(f"added #{card_id}: {filename} -> {name} (diamond)")
        added += 1

print(f"done — {added} added, {renamed} renamed")
