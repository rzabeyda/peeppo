"""
Peeppo — one-off catalog cleanup script (run once on the server, then it can be deleted).

Fixes:
  - merges the duplicate "Big Ben Clock" card (id 164, the corrupted-filename copy
    left over from before the rename) into id 165 (the correct file), moving any
    inventory copies over first so nothing gets orphaned, then removes id 164
  - fixes a batch of catalog display-name typos/preferences
  - assigns rarity (rare/epic/legend) to the whole catalog — everything not listed in
    RARITIES stays the schema default ('rare')

Usage (from the server, in the peeppo directory):
    python3 fix_cards.py
"""

import database as db

RENAMES = {
    109: "Peashooter",        # was "Pea Shooter"
    160: "WarCraft",          # was "Warcraft"
    165: "Big Ben",           # was "Big Ben Clock" (the surviving copy after the merge below)
    120: "Red Alert",        # was "Redalert"
    116: "Ray-Ban Meta",      # was "Ray Ban Meta"
    68: "Hypno Lollipop",     # was "Hypno Lollipot" (typo)
    62: "Pepe Green",         # was "Green Pepe"
    18: "Pepe Black",         # was "Black Pepe"
    59: "Pigeon",             # was "Golub"
    56: "Golan",              # was "Golan The Insatiable"
    21: "Beaver",             # was "Bober"
    15: "BigMac",             # was "Bicmac"
    85: "Lineage",            # was "Linaage" (typo)
    27: "Briefcase",          # was "Business Briefcase" (shortened)
}

DUPLICATE_KEEP_ID = 165   # big_ben_clock.jpg (correct file)
DUPLICATE_DROP_ID = 164   # иig_иen_clock.jpg (corrupted-filename leftover)

# ids not listed here default to "rare" (the schema default)
RARITIES = {
    # --- legend (10) ---
    9: "legend",    # Astral Shard
    25: "legend",   # BTC
    37: "legend",   # Diamond Ring
    40: "legend",   # Durovs Cap
    48: "legend",   # Ethereum
    58: "legend",   # Gold Might Arm
    94: "legend",   # Mini Oscar
    149: "legend",  # Swiss Watch
    18: "legend",   # Pepe Black
    49: "legend",   # Euro
    62: "legend",   # Pepe Green
    74: "legend",   # iPhone
    98: "legend",   # Nail Bracelet
    10: "legend",   # Audi
    20: "legend",   # BMW
    38: "legend",   # Dollars
    39: "legend",   # Dubai
    70: "legend",   # Intelligence Cup
    71: "legend",   # Ion Gem
    92: "legend",   # Mercedes
    121: "legend",  # Redo
    127: "legend",  # Roman Helmet
    150: "legend",  # Tesla
    # --- epic (50) ---
    55: "epic",     # Genie Lamp
    4: "epic",      # Air Jordan 1
    6: "epic",      # AK-47
    7: "epic",      # Algorithm Cup
    17: "epic",     # Black Might Arm
    22: "epic",     # Bonded Ring
    31: "epic",     # Cigar
    33: "epic",     # Crystall Ball
    44: "epic",     # Eiffel Tower
    45: "epic",     # Electric Skull
    46: "epic",     # Eternal Candle
    47: "epic",     # Eternal Rose
    53: "epic",     # Gem
    54: "epic",     # Gem Signet
    57: "epic",     # Gold Bling Binky
    66: "epic",     # Heart Locket
    81: "epic",     # Kissed Frog
    82: "epic",     # Kremlin
    86: "epic",     # Loot Bag
    88: "epic",     # Love Potion
    91: "epic",     # Magic Potion
    95: "epic",     # Money Pot
    101: "epic",    # Nike Dunk Low
    116: "epic",    # Ray-Ban Meta
    122: "epic",    # Revolver
    134: "epic",    # Signet Ring
    137: "epic",    # Skull Flower
    138: "epic",    # Sky Stilettos
    146: "epic",    # Stanley Quencher
    152: "epic",    # Top Hat
    154: "epic",    # Trapped Heart
    159: "epic",    # Voodoo Doll
    165: "epic",    # Big Ben
    8: "epic",      # Android
    69: "epic",     # iMac
    73: "epic",     # iPad
    75: "epic",     # iPod
    89: "epic",     # Low Rider
    103: "epic",    # Nintendo
    108: "epic",    # Papakha
    111: "epic",    # Perfume Bottle
    117: "epic",    # Record Player
    132: "epic",    # Scooter
    161: "epic",    # Westside Sign
    163: "epic",    # Xbox
    142: "epic",    # Solana
    2: "epic",      # Adidas Samba
    5: "epic",      # Airpods
    27: "epic",     # Briefcase (was Business Briefcase)
    100: "epic",    # Neko Helmet
    102: "epic",    # Nike Shoes
    104: "epic",    # Nokia
    113: "epic",    # Playstation
    131: "epic",    # Scared Cat
    # --- explicit downgrades to rare (idempotent even if an earlier run set epic/legend) ---
    24: "rare",     # Brass Knuckles
    36: "rare",     # Diablo
    50: "rare",     # Flying Broom
    56: "rare",     # Golan
    60: "rare",     # Gram
    65: "rare",     # Harry Potter
    99: "rare",     # Naruto
    115: "rare",    # Rare Bird
    123: "rare",    # Rick And Morty
    125: "rare",    # Rocket
}


def main():
    db.init_db()
    with db.get_conn() as conn:
        # --- merge the duplicate Big Ben Clock card ---
        drop_row = conn.execute("SELECT id, filename FROM cards WHERE id = ?", (DUPLICATE_DROP_ID,)).fetchone()
        keep_row = conn.execute("SELECT id, filename FROM cards WHERE id = ?", (DUPLICATE_KEEP_ID,)).fetchone()
        if drop_row and keep_row:
            moved = conn.execute(
                "UPDATE user_cards SET card_id = ? WHERE card_id = ?",
                (DUPLICATE_KEEP_ID, DUPLICATE_DROP_ID),
            ).rowcount
            conn.execute("DELETE FROM cards WHERE id = ?", (DUPLICATE_DROP_ID,))
            print(f"merged duplicate card #{DUPLICATE_DROP_ID} ({drop_row['filename']}) into "
                  f"#{DUPLICATE_KEEP_ID} ({keep_row['filename']}) — moved {moved} owned copy(ies)")
        else:
            print(f"skip duplicate merge — card #{DUPLICATE_DROP_ID} or #{DUPLICATE_KEEP_ID} not found")

        # --- rename the rest ---
        for card_id, new_name in RENAMES.items():
            row = conn.execute("SELECT name FROM cards WHERE id = ?", (card_id,)).fetchone()
            if row is None:
                print(f"skip #{card_id} — not found")
                continue
            conn.execute("UPDATE cards SET name = ? WHERE id = ?", (new_name, card_id))
            print(f"#{card_id}: {row['name']!r} -> {new_name!r}")

        # --- assign rarity ---
        for card_id, rarity in RARITIES.items():
            row = conn.execute("SELECT name FROM cards WHERE id = ?", (card_id,)).fetchone()
            if row is None:
                print(f"skip rarity for #{card_id} — not found")
                continue
            conn.execute("UPDATE cards SET rarity = ? WHERE id = ?", (rarity, card_id))
        print(f"assigned rarity to {len(RARITIES)} cards (everything else stays 'rare')")

    print("done")


if __name__ == "__main__":
    main()
