"""
One-off: registers the 3rd big NFT batch (41 items) in the catalog at platinum rarity.
Idempotent — safe to run more than once, already-registered filenames are skipped.

Usage (from the server, after scp-ing the images into static/cards/):
    python3 add_platinum_batch3.py
"""

import database as db
from add_card import name_for

FILENAMES = [
    "teapot_silver.jpg", "skateboard_art.jpg", "briefcase_wood.jpg", "mask_fencing.jpg",
    "cufflinks_gold.jpg", "lamp_artdeco.jpg", "decanter_crystal.jpg", "briefcase_carbon.jpg",
    "minibar_globe.jpg", "telescope_brass.jpg", "pipes_audio.jpg", "globe_gemstone.jpg",
    "firearm_engrave.jpg", "cigar_humidor.jpg", "projector_4k.jpg", "drone_cinema.jpg",
    "briefcase_calf.jpg", "knife_damascus.jpg", "camera_leica.jpg", "helmet_moto.jpg",
    "skate_gold.jpg", "pen_fountain.jpg", "motor_scooter.jpg", "turntable_hifi.jpg",
    "bottle_cognac.jpg", "chess_mammoth.jpg", "glasses_diamond.jpg", "surf_carbon.jpg",
    "handbag_croc.jpg", "audio_horn.jpg", "coffee_beast.jpg", "bike_carbon.jpg",
    "guitar_strat.jpg", "jacket_python.jpg", "chair_eames.jpg", "car_restomod.jpg",
    "watch_vintage.jpg", "sofa_velvet.jpg", "sneakers_gold.jpg", "meteorite_vault.jpg",
    "nasa_voyager.jpg",
]

with db.get_conn() as conn:
    known = {row["filename"] for row in conn.execute("SELECT filename FROM cards")}

added = 0
for filename in FILENAMES:
    if filename in known:
        print(f"skip (already registered): {filename}")
        continue
    name = name_for(filename)
    card_id = db.add_card_to_catalog(filename, name)
    with db.get_conn() as conn:
        conn.execute("UPDATE cards SET rarity = 'platinum' WHERE id = ?", (card_id,))
    print(f"added #{card_id}: {filename} -> {name} (platinum)")
    added += 1

print(f"done — {added} new card(s) registered as platinum (of {len(FILENAMES)} in batch)")
