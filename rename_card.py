"""
One-off: renames the "Counter T" card to "SAS".
Finds the card by a case-insensitive match on its current name (handles
"Counter T", "Counter-T", "Counter Terrorist", etc.) rather than assuming
the exact spelling, and only renames when exactly one card matches.

Usage (on the server):
    python3 rename_card.py
"""

import database as db

SEARCH = "%counter%"
NEW_NAME = "SAS"

with db.get_conn() as conn:
    matches = conn.execute(
        "SELECT id, filename, name FROM cards WHERE name LIKE ? COLLATE NOCASE", (SEARCH,)
    ).fetchall()

    if not matches:
        print("no card found with 'counter' in its name")
    elif len(matches) > 1:
        print("multiple matches, not renaming automatically — pick one:")
        for m in matches:
            print(f"  #{m['id']}  {m['filename']!r}  name={m['name']!r}")
    else:
        m = matches[0]
        conn.execute("UPDATE cards SET name = ? WHERE id = ?", (NEW_NAME, m["id"]))
        print(f"renamed #{m['id']} ({m['filename']}): {m['name']!r} -> {NEW_NAME!r}")
