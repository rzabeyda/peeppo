"""
One-off: registers new luxury/diamond-rarity cards in the catalog.
Name is always exactly the humanized filename (add_card.py's name_for()).
Idempotent — safe to run more than once, already-registered filenames just get renamed+re-tiered.

Usage (from the server, after scp-ing the images into static/cards/):
    python3 add_diamond_cards.py
"""

import database as db
from add_card import name_for

FILENAMES = [
    "airbus.jpg",
    "airbus_exclusive.jpg",
    "alpine_apex.jpg",
    "azzam.jpg",
    "billionaire_vodka.jpg",
    "bombardier.jpg",
    "bugati_chiron.jpg",
    "bugatti_brouillard.jpg",
    "bugatti_la_noire.jpg",
    "card_players.jpg",
    "cliffside_estate.jpg",
    "damalfi_limoncello.jpg",
    "dibar.jpg",
    "dreamliner.jpg",
    "eclipse.jpg",
    "empathy_suite.jpg",
    "fregate_island.jpg",
    "gulfstream.jpg",
    "henri_iv_cognac.jpg",
    "interchange.jpg",
    "koru.jpg",
    "kyoto_zen.jpg",
    "lanai.jpg",
    "laucala_island.jpg",
    "lovers_deep.jpg",
    "macallan_1926.jpg",
    "mark_penthouse.jpg",
    "mona_lisa.jpg",
    "nafea_faa_Ipoipo.jpg",
    "necker_island.jpg",
    "north_island.jpg",
    "number_17a.jpg",
    "pagani_barchetta.jpg",
    "palm_villa.jpg",
    "royal_mansion.jpg",
    "rr_amethyst.jpg",
    "rr_arcadia.jpg",
    "rr_boat_tail.jpg",
    "rr_la_rose.jpg",
    "rr_sweptail.jpg",
    "sa_ferradura.jpg",
    "sailing.jpg",
    "salvator_mundi.jpg",
    "sp_chaos.jpg",
    "tequila_ley.jpg",
    "tuscan_fortrees.jpg",
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
