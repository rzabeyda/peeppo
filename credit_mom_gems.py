import sqlite3

USERNAME = "zzabeyda"
AMOUNT = 100_000

conn = sqlite3.connect("peeppo.db")
conn.row_factory = sqlite3.Row

row = conn.execute(
    "SELECT telegram_id, username, first_name, gems, gems_earned FROM users WHERE username = ? COLLATE NOCASE",
    (USERNAME,),
).fetchone()

if row is None:
    print(f"no user found with username @{USERNAME} -- check spelling / that she's opened the bot at least once")
else:
    print("before:", dict(row))
    conn.execute(
        "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? WHERE telegram_id = ?",
        (AMOUNT, AMOUNT, row["telegram_id"]),
    )
    conn.commit()
    after = conn.execute(
        "SELECT telegram_id, username, first_name, gems, gems_earned FROM users WHERE telegram_id = ?",
        (row["telegram_id"],),
    ).fetchone()
    print("after:", dict(after))
    print(f"credited {AMOUNT} gems to @{USERNAME}")
