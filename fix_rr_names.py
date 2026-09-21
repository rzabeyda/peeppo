"""
One-off: fixes the Rolls-Royce cards whose names got humanized as "Rr ..."
before "rr" was added to add_card.py's ACRONYMS — renames them to "RR ...".
"""

import database as db

FILENAMES = [
    "rr_amethyst.jpg",
    "rr_arcadia.jpg",
    "rr_boat_tail.jpg",
    "rr_la_rose.jpg",
    "rr_sweptail.jpg",
]

with db.get_conn() as conn:
    for filename in FILENAMES:
        row = conn.execute("SELECT id, name FROM cards WHERE filename = ?", (filename,)).fetchone()
        if not row:
            print(f"not found: {filename}")
            continue
        if not row["name"] or not row["name"].startswith("Rr "):
            print(f"skip (name doesn't start with 'Rr '): #{row['id']} {row['name']!r}")
            continue
        new_name = "RR " + row["name"][3:]
        conn.execute("UPDATE cards SET name = ? WHERE id = ?", (new_name, row["id"]))
        print(f"#{row['id']} ({filename}): {row['name']!r} -> {new_name!r}")
