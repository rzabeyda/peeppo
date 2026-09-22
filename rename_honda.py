"""
One-off: renames the "Honda motocycle" card to the shorter "Honda Mcycle".
Idempotent — safe to run more than once (no-op if already renamed / not found).

Usage (on the server):
    python3 rename_honda.py
"""

import database as db

OLD_NAME = "Honda motocycle"
NEW_NAME = "Honda Mcycle"

with db.get_conn() as conn:
    rows = conn.execute(
        "SELECT id, filename, name FROM cards WHERE name LIKE ?",
        (f"%honda%",),
    ).fetchall()

if not rows:
    print("no card with 'honda' in the name found")
else:
    for r in rows:
        print(f"found #{r['id']}: {r['filename']} -> \"{r['name']}\"")
    exact = [r for r in rows if r["name"].strip().lower() == OLD_NAME.lower()]
    if not exact:
        print(f"none matched exactly \"{OLD_NAME}\" — rename the right id manually if needed")
    else:
        with db.get_conn() as conn:
            for r in exact:
                conn.execute("UPDATE cards SET name = ? WHERE id = ?", (NEW_NAME, r["id"]))
                print(f"renamed #{r['id']}: \"{r['name']}\" -> \"{NEW_NAME}\"")
