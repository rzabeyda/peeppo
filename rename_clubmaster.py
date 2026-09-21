"""
One-off: rename "Ray-Ban Clubmaster" (matched by name, filename may vary) to "Ray-Ban CM".
"""

import database as db

with db.get_conn() as conn:
    row = conn.execute(
        "SELECT id, filename, name FROM cards WHERE name LIKE '%clubmaster%' COLLATE NOCASE"
    ).fetchone()
    if not row:
        print("no card found with 'clubmaster' in its name")
    else:
        conn.execute("UPDATE cards SET name = ? WHERE id = ?", ("Ray-Ban CM", row["id"]))
        print(f"#{row['id']} ({row['filename']}): {row['name']!r} -> 'Ray-Ban CM'")
