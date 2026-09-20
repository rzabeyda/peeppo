"""
One-off: registers/renames cards in the catalog — name is always exactly the
humanized filename (same rule as add_card.py's --scan), rarity bronze.
Safe to run more than once:
  - filename not in the catalog yet -> inserted and set to bronze
  - filename already in the catalog -> just renamed to match the filename
    (covers the earlier batch that got accidentally named in Russian)

Usage (from the server, after scp-ing the images into static/cards/):
    python3 add_new_cards.py
"""

import database as db
from add_card import name_for

FILENAMES = [
    "dipper_pines.png",
    "mabel.jpg",
    "waddles.jpg",
    "roman_helmet.jpg",
    "rubiks_cube.jpg",
    "eternal_rose.jpg",
    "dendy.jpg",
    "among_us.jpg",
    "archangel.jpg",
    "arthas_menethil.jpg",
    "barbarian.jpg",
    "battle_toad.jpg",
    "bibi.jpg",
    "bill_rizer.jpg",
    "boss.jpg",
    "brainrotini.jpg",
    "bugs_bunny.jpg",
    "caleb.jpg",
    "carmagedon.jpg",
    "colt.jpg",
    "deckard_cain.jpg",
    "diablo.jpg",
    "doomguy.jpg",
    "dragon.jpg",
    "duck_hunt.jpg",
    "duffy_duck.jpg",
    "duke_nukem.jpg",
    "edgar.jpg",
    "el_primo.jpg",
    "emz.jpg",
    "freeman.jpg",
    "frodo.jpg",
    "galaxy.jpg",
    "godzooky.jpg",
    "goro.jpg",
    "griffon.jpg",
    "haggie_vaggie.jpg",
    "jerry.jpg",
    "jim_raynor.jpg",
    "karlson.jpg",
    "king_kong.jpg",
    "leon.jpg",
    "lode_runner.jpg",
    "mario.jpg",
    "max.jpg",
    "max_payne.jpg",
    "minion.jpg",
    "moana.jpg",
    "mortis.jpg",
    "pac_man.jpg",
    "peasant.jpg",
    "peashoter.jpg",
    "pegas.jpg",
    "pickaxe.jpg",
    "pikachu.jpg",
    "quake.jpg",
    "queen.jpg",
    "raven.jpg",
    "sim.jpg",
    "skeleton.jpg",
    "sneakshark.jpg",
    "sonic.jpg",
    "sorceress.jpg",
    "spider_man.jpg",
    "spike.jpg",
    "stanley_quencher.jpg",
    "thrall.jpg",
    "tom.jpg",
    "tomb_raider.jpg",
    "tommy_vercetti.jpg",
    "troglodyte.jpg",
    "tyrael.jpg",
    "unicorn.jpg",
    "vampire.jpg",
    "wolf.jpg",
    "wolfrine.jpg",
    "worm.jpg",
    "zealot.jpg",
    "zergling.jpg",
    "zombie.jpg",
]

with db.get_conn() as conn:
    known = {row["filename"]: row["id"] for row in conn.execute("SELECT id, filename FROM cards")}

added, renamed = 0, 0
for filename in FILENAMES:
    name = name_for(filename)
    if filename in known:
        with db.get_conn() as conn:
            conn.execute("UPDATE cards SET name = ?, rarity = 'bronze' WHERE id = ?", (name, known[filename]))
        print(f"renamed #{known[filename]}: {filename} -> {name}")
        renamed += 1
    else:
        card_id = db.add_card_to_catalog(filename, name)
        with db.get_conn() as conn:
            conn.execute("UPDATE cards SET rarity = 'bronze' WHERE id = ?", (card_id,))
        print(f"added #{card_id}: {filename} -> {name} (bronze)")
        added += 1

print(f"done — {added} added, {renamed} renamed")

# bunny removed from the game entirely — deactivate it in case an earlier run registered it
with db.get_conn() as conn:
    row = conn.execute("SELECT id FROM cards WHERE filename = ?", ("bunny.jpg",)).fetchone()
    if row:
        conn.execute("UPDATE cards SET is_active = 0 WHERE id = ?", (row["id"],))
        print(f"deactivated bunny.jpg (#{row['id']})")
    else:
        print("bunny.jpg was not registered — nothing to deactivate")
