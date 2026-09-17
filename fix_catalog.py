"""
One-off cleanup: removes any non-image junk (like .gitkeep) that got scanned
into the card catalog before add_card.py filtered by extension.

Usage: python3 fix_catalog.py
"""

import database as db

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")

with db.get_conn() as conn:
    rows = conn.execute("SELECT id, filename FROM cards").fetchall()
    junk = [r for r in rows if not r["filename"].lower().endswith(IMAGE_EXTS)]
    for r in junk:
        conn.execute("DELETE FROM cards WHERE id = ?", (r["id"],))
        print(f"removed junk card #{r['id']}: {r['filename']}")
    print(f"done — removed {len(junk)} junk row(s), {len(rows) - len(junk)} real card(s) remain")
