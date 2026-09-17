"""
One-off: full reset for a brand-new card set.

Wipes:
  - cards            (the whole catalog — every card definition)
  - user_cards        (every farmed/owned copy, for every player)
  - market_offers     (every pending/past offer, since they reference user_cards)

Resets the AUTOINCREMENT counters for cards and user_cards, so once you add the
new card images the catalog IDs and the farm drop numbering both start fresh at 1.

This does NOT touch users, gems balances, or referral counts — only the card
catalog and everything that points at specific card copies.

Run this on the SERVER, in /root/peeppo, then separately delete the old image
files from static/cards/ (see the accompanying instructions).

Usage: python3 wipe_cards.py
"""

import database as db

with db.get_conn() as conn:
    offers = conn.execute("SELECT COUNT(*) AS n FROM market_offers").fetchone()["n"]
    copies = conn.execute("SELECT COUNT(*) AS n FROM user_cards").fetchone()["n"]
    catalog = conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"]

    conn.execute("DELETE FROM market_offers")
    conn.execute("DELETE FROM user_cards")
    conn.execute("DELETE FROM cards")
    conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('user_cards', 'cards', 'market_offers')")

    print(f"wiped {catalog} card(s) from the catalog")
    print(f"wiped {copies} farmed cop{'y' if copies == 1 else 'ies'} across all players")
    print(f"wiped {offers} market offer(s)")
    print("counters reset — next card added gets id 1, next farm drop gets #1")
