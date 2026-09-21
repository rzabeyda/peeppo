"""
One-off: shorten a handful of card display names.
"""

import database as db

RENAMES = {
    "american_platinum.jpg": "Amex Platinum",
    "chopard_blue_diamond.jpg": "Blue Diamond",
    "damalfi_limoncello.jpg": "Limoncello",
    "jpmorgan_reserve.jpg": "JP Morgan",
}

with db.get_conn() as conn:
    for filename, new_name in RENAMES.items():
        row = conn.execute("SELECT id, name FROM cards WHERE filename = ?", (filename,)).fetchone()
        if not row:
            print(f"not found: {filename}")
            continue
        conn.execute("UPDATE cards SET name = ? WHERE id = ?", (new_name, row["id"]))
        print(f"#{row['id']} ({filename}): {row['name']!r} -> {new_name!r}")
