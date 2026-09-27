"""
One-off: forces number 7 into 'owned' by the admin outright (as if they'd just won
the auction), refunding any current highest bidder's escrowed gems first so nothing
gets silently lost or duplicated if number 7 happened to be mid-auction.

Run once from /root/peeppo on the server: python3 win_number_7.py
"""
import os
import sqlite3
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()
ADMIN_ID = int(os.environ["ADMIN_ID"])

conn = sqlite3.connect("peeppo.db")
conn.row_factory = sqlite3.Row

row = conn.execute("SELECT * FROM card_numbers WHERE number = 7").fetchone()
print("before:", dict(row) if row else "no row yet (was untracked/free)")

if row is not None and row["status"] == "auction" and row["highest_bidder_id"] is not None:
    conn.execute(
        "UPDATE users SET gems = gems + ? WHERE telegram_id = ?",
        (row["highest_bid"], row["highest_bidder_id"]),
    )
    print(f"refunded {row['highest_bid']} gems to bidder {row['highest_bidder_id']}")

now = datetime.now(timezone.utc).isoformat()
conn.execute(
    """
    INSERT INTO card_numbers (number, status, owner_id, user_card_id, updated_at)
    VALUES (7, 'owned', ?, NULL, ?)
    ON CONFLICT(number) DO UPDATE SET
        status = 'owned', owner_id = excluded.owner_id, user_card_id = NULL,
        highest_bid = NULL, highest_bidder_id = NULL, bid_expires_at = NULL,
        list_price = NULL, updated_at = excluded.updated_at
    """,
    (ADMIN_ID, now),
)
conn.commit()

after = conn.execute("SELECT * FROM card_numbers WHERE number = 7").fetchone()
print("after:", dict(after))

conn.close()
