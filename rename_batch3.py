"""
One-off: rename a handful of card display names. Idempotent -- matches by filename,
so re-running is harmless (just no-ops once already renamed).

Usage (on the server):
    python3 rename_batch3.py
"""

import database as db

RENAMES = {
    "cadillacs_dino.jpg": "Cadillacs Dino",
    "headphone_stand.jpg": "Headphone ST",
    "motorcycle_honda.jpg": "Honda MC",
}

with db.get_conn() as conn:
    for filename, new_name in RENAMES.items():
        row = conn.execute("SELECT id, name FROM cards WHERE filename = ?", (filename,)).fetchone()
        if not row:
            print(f"not found: {filename}")
            continue
        if row["name"] == new_name:
            print(f"#{row['id']} ({filename}): already {new_name!r}")
            continue
        conn.execute("UPDATE cards SET name = ? WHERE id = ?", (new_name, row["id"]))
        print(f"#{row['id']} ({filename}): {row['name']!r} -> {new_name!r}")
