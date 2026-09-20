"""
One-off: move specific diamond-rarity cards down to platinum.
Safe to run more than once — just re-sets rarity, no-op if already platinum.
"""

import database as db

FILENAMES = [
    "birkin_20_sellier.jpg",
    "birkin_25_sellier.jpg",
    "birkin_himalaya30.jpg",
    "patek_philippe_5711.jpg",
    "patek_philippe_5811.jpg",
    "gelandewagen.jpg",
    "porsche_911.jpg",
    "lamba.jpg",
]

with db.get_conn() as conn:
    for filename in FILENAMES:
        row = conn.execute("SELECT id, name, rarity FROM cards WHERE filename = ?", (filename,)).fetchone()
        if not row:
            print(f"not found: {filename}")
            continue
        conn.execute("UPDATE cards SET rarity = 'platinum' WHERE id = ?", (row["id"],))
        print(f"#{row['id']} {row['name']} ({filename}): {row['rarity']} -> platinum")

print("done")
