import sys
sys.path.insert(0, "/root/peeppo")
import database as db
with db.get_conn() as conn:
    rows = conn.execute(
        """SELECT uc.id FROM user_cards uc JOIN cards c ON c.id = uc.card_id
           WHERE uc.voided = 0 AND uc.custom_name IS NULL AND uc.number_override IS NULL
             AND c.filename LIKE '../nft/%' ORDER BY uc.obtained_at, uc.id"""
    ).fetchall()
    out = []
    for r in rows:
        n = db._next_free_number(conn)
        conn.execute("UPDATE user_cards SET number_override = ? WHERE id = ?", (n, r["id"]))
        conn.execute("DELETE FROM card_numbers WHERE number = ? AND status = 'free'", (n,))
        out.append(n)
print("renumbered new cards:", len(out), "numbers:", out[:30])
