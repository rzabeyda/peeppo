import sqlite3

conn = sqlite3.connect("/root/peeppo/peeppo.db")
cur = conn.execute(
    "UPDATE users SET gems = 10000 WHERE LOWER(username) = LOWER('rzabeyda')"
)
conn.commit()
print("rows changed:", cur.rowcount)
row = conn.execute(
    "SELECT telegram_id, username, gems FROM users WHERE LOWER(username) = LOWER('rzabeyda')"
).fetchone()
print("after:", row)
conn.close()
