"""
One-off: renames the "Underground Helm" card (filename underground_helm.jpg) to the
shorter "UG Helm". Idempotent — safe to run more than once (no-op if already renamed
or the card doesn't exist yet).

Usage (on the server):
    python3 rename_ug_helm.py
"""

import database as db

NEW_NAME = "UG Helm"

with db.get_conn() as conn:
    row = conn.execute(
        "SELECT id, filename, name FROM cards WHERE filename = ?",
        ("underground_helm.jpg",),
    ).fetchone()

if row is None:
    print("underground_helm.jpg not registered yet — nothing to rename (name override is already set for next time it's added)")
elif row["name"] == NEW_NAME:
    print(f"#{row['id']} already named \"{NEW_NAME}\" — nothing to do")
else:
    with db.get_conn() as conn:
        conn.execute("UPDATE cards SET name = ? WHERE id = ?", (NEW_NAME, row["id"]))
    print(f"renamed #{row['id']}: \"{row['name']}\" -> \"{NEW_NAME}\"")
