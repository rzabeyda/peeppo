"""
One-off: fully erase the "yacht" card. Anyone who owns a copy gets it swapped
for a random other active diamond card instead of just losing it/getting gems.
"""

import random

import database as db

FILENAME = "yacht.jpg"

with db.get_conn() as conn:
    card = conn.execute("SELECT id, name FROM cards WHERE filename = ?", (FILENAME,)).fetchone()
    if not card:
        print(f"not found: {FILENAME}")
        raise SystemExit(0)

    pool = [
        r["id"] for r in conn.execute(
            "SELECT id FROM cards WHERE is_active = 1 AND rarity = 'diamond' AND id != ?",
            (card["id"],),
        )
    ]
    if not pool:
        print("no other active diamond cards to swap into — aborting, nothing changed")
        raise SystemExit(1)

    owners = conn.execute("SELECT id, user_id FROM user_cards WHERE card_id = ?", (card["id"],)).fetchall()
    for row in owners:
        replacement = random.choice(pool)
        conn.execute("UPDATE user_cards SET card_id = ? WHERE id = ?", (replacement, row["id"]))

    print(f"swapped {len(owners)} owned copy(ies) of {card['name']} to random other diamond cards")

    # now nobody references it — safe to erase the row entirely
    conn.execute("DELETE FROM cards WHERE id = ?", (card["id"],))
    print(f"deleted card #{card['id']} ({FILENAME}) from the catalog for good")
