import sys
sys.path.insert(0, "/root/peeppo")
import database as db

BOT_ID = -1
BOT_USERNAME = "test_bot"

def ensure_bot_user():
    with db.get_conn() as conn:
        row = conn.execute("SELECT telegram_id FROM users WHERE telegram_id = ?", (BOT_ID,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users (telegram_id, username, first_name, gems, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (BOT_ID, BOT_USERNAME, "Тест-бот", 999999, db._now()),
            )
            print(f"created bot user {BOT_ID}")
        else:
            print(f"bot user {BOT_ID} already exists")

def ensure_bot_card():
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM user_cards WHERE user_id = ? AND listed_price IS NULL AND swap_listed = 0 "
            "AND staked_at IS NULL AND pvp_round_id IS NULL AND pinned_at IS NULL LIMIT 1",
            (BOT_ID,),
        ).fetchone()
        if row:
            print(f"reusing existing free bot card, user_card_id={row['id']}")
            return row["id"]
        card_row = conn.execute("SELECT id FROM cards WHERE is_active = 1 ORDER BY id LIMIT 1").fetchone()
        if card_row is None:
            raise SystemExit("no cards exist in catalog -- can't give the bot one")
        cur = conn.execute(
            "INSERT INTO user_cards (user_id, card_id, obtained_at) VALUES (?, ?, ?)",
            (BOT_ID, card_row["id"], db._now()),
        )
        print(f"gave bot a fresh card, user_card_id={cur.lastrowid}")
        return cur.lastrowid

ensure_bot_user()
ucid = ensure_bot_card()
result = db.join_pvp_round(BOT_ID, [ucid])
print("bot joined PvP round:", result)
