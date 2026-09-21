"""
Retire an NFT from the game for good — just deactivates it in the catalog
(hidden from Models / no longer dropped). Gem compensation to current owners
(+25 gems per copy held) now happens automatically via a DB trigger the
moment is_active flips to 0, so this script doesn't need to touch gems itself.

Usage:
    python3 retire_card.py <filename>
Example:
    python3 retire_card.py bunny.jpg
"""

import sys

import database as db

if len(sys.argv) != 2:
    print("usage: python3 retire_card.py <filename>")
    sys.exit(1)

filename = sys.argv[1]

with db.get_conn() as conn:
    card = conn.execute("SELECT id, name, is_active FROM cards WHERE filename = ?", (filename,)).fetchone()
    if not card:
        print(f"not found: {filename}")
        sys.exit(1)
    if card["is_active"] == 0:
        print(f"already retired: #{card['id']} {card['name']} ({filename})")
        sys.exit(0)

    owners = conn.execute(
        "SELECT COUNT(*) AS n FROM user_cards WHERE card_id = ?", (card["id"],)
    ).fetchone()["n"]

    conn.execute("UPDATE cards SET is_active = 0 WHERE id = ?", (card["id"],))
    print(f"retired #{card['id']} {card['name']} ({filename}) — "
          f"{owners} owned copy(ies) auto-compensated 25 gems each")
