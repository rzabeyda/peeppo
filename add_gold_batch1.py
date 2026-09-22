"""
One-off: registers the new 40-item NFT batch in the catalog at gold rarity.
Idempotent — safe to run more than once, already-registered filenames are skipped.

Usage (from the server, after scp-ing the images into static/cards/):
    python3 add_gold_batch1.py
"""

import database as db
from add_card import name_for

FILENAMES = [
    "chopper_bike.jpg", "cyber_kicks.jpg", "steam_lamp.jpg", "dual_mics.jpg", "secure_wallet.jpg",
    "retro_deck.jpg", "kinetic_ball.jpg", "synth_box.jpg", "metal_chess.jpg", "pro_multitool.jpg",
    "silicon_key.jpg", "ar_glasses.jpg", "rebel_jacket.jpg", "sound_cans.jpg", "tactical_vest.jpg",
    "gear_board.jpg", "matrix_watch.jpg", "underground_helm.jpg", "pro_mic.jpg", "pocket_play.jpg",
    "vault_capsule.jpg", "custom_blade.jpg", "gold_chip.jpg", "cyber_typewriter.jpg", "urban_pack.jpg",
    "safe_cube.jpg", "holo_projector.jpg", "secure_router.jpg", "genesis_core.jpg", "custom_kb.jpg",
    "vr_visor.jpg", "scout_drone.jpg", "heavy_lighter.jpg", "headphone_stand.jpg", "matrix_ring.jpg",
    "pro_case.jpg", "matrix_shades.jpg", "cyber_crossbow.jpg", "matrix_compass.jpg", "desk_clock.jpg",
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
        conn.execute("UPDATE cards SET rarity = 'gold' WHERE id = ?", (card_id,))
    print(f"added #{card_id}: {filename} -> {name} (gold)")
    added += 1

print(f"done — {added} new card(s) registered as gold (of {len(FILENAMES)} in batch)")
