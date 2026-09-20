"""
One-off: remove the "jet" NFT from the game (soft-delete).
"""

import database as db

FILENAME = "jet.jpg"

with db.get_conn() as conn:
    row = conn.execute("SELECT id, name FROM cards WHERE filename = ?", (FILENAME,)).fetchone()
    if not row:
        print(f"not found: {FILENAME}")
    else:
        conn.execute("UPDATE cards SET is_active = 0 WHERE id = ?", (row["id"],))
        print(f"deactivated #{row['id']} {row['name']} ({FILENAME})")
