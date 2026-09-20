import database as db

FILENAME = "call_of_duty.jpg"
NEW_NAME = "John Price"

with db.get_conn() as conn:
    row = conn.execute("SELECT id, name FROM cards WHERE filename = ?", (FILENAME,)).fetchone()
    if not row:
        print(f"no card found with filename {FILENAME!r}")
    else:
        conn.execute("UPDATE cards SET name = ? WHERE id = ?", (NEW_NAME, row["id"]))
        print(f"renamed #{row['id']} ({FILENAME}): {row['name']!r} -> {NEW_NAME!r}")
