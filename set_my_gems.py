import re, sqlite3
env = open("/root/peeppo/.env").read()
admin = int(re.search(r"^ADMIN_ID=(\d+)", env, re.M).group(1))
c = sqlite3.connect("/root/peeppo/peeppo.db", timeout=30)
old = c.execute("SELECT gems FROM users WHERE telegram_id=?", (admin,)).fetchone()[0]
c.execute("UPDATE users SET gems=100000 WHERE telegram_id=?", (admin,))
c.commit()
new = c.execute("SELECT gems FROM users WHERE telegram_id=?", (admin,)).fetchone()[0]
print("admin:", admin, "old:", old, "new:", new)
