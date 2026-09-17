"""
Peeppo — one-off catalog cleanup script (run once on the server, then it can be deleted).

Fixes:
  - merges the duplicate "Big Ben Clock" card (id 164, the corrupted-filename copy
    left over from before the rename) into id 165 (the correct file), moving any
    inventory copies over first so nothing gets orphaned, then removes id 164
  - fixes a batch of catalog display-name typos/preferences

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
}

DUPLICATE_KEEP_ID = 165   # big_ben_clock.jpg (correct file)
DUPLICATE_DROP_ID = 164   # иig_иen_clock.jpg (corrupted-filename leftover)


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

    print("done")


if __name__ == "__main__":
    main()
