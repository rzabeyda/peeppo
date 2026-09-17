"""
One-off: wipes ALL farmed cards from ALL users (user_cards table) and resets the
autoincrement counter, so the next farm anywhere starts back at drop #1.

The card catalog itself (cards table — the 50 registered images) is untouched.

Usage: python3 reset_inventory.py
"""

import database as db

with db.get_conn() as conn:
    before = conn.execute("SELECT COUNT(*) AS n FROM user_cards").fetchone()["n"]
    conn.execute("DELETE FROM user_cards")
    conn.execute("DELETE FROM sqlite_sequence WHERE name = 'user_cards'")
    print(f"wiped {before} inventory row(s) — next farm anywhere will be drop #1")
