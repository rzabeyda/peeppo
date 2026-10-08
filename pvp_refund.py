import re, sqlite3
env = open("/root/peeppo/.env").read()
admin = int(re.search(r"^ADMIN_ID=(\d+)", env, re.M).group(1))
c = sqlite3.connect("/root/peeppo/peeppo.db", timeout=30)
rounds = [r[0] for r in c.execute("SELECT id FROM pvp_rounds WHERE status='open'")]
n = 0
for rid in rounds:
    ids = [r[0] for r in c.execute("SELECT user_card_id FROM pvp_entries WHERE round_id=? AND user_id=?", (rid, admin))]
    for ucid in ids:
        c.execute("UPDATE user_cards SET pvp_round_id=NULL WHERE id=? AND user_id=?", (ucid, admin))
    c.execute("DELETE FROM pvp_entries WHERE round_id=? AND user_id=?", (rid, admin))
    left = c.execute("SELECT COUNT(DISTINCT user_id) FROM pvp_entries WHERE round_id=?", (rid,)).fetchone()[0]
    if left < 2:
        c.execute("UPDATE pvp_rounds SET lock_at=NULL WHERE id=?", (rid,))
    n += len(ids)
c.commit()
print("admin:", admin, "returned cards:", n)
