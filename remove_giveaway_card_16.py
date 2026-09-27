"""
One-off: pulls card #16 (Cybertruck, user_card_id=109, number_giveaways.id=322) out of
the currently-live /cardsgiveaway batch (id=2, 365 cards, drawn_at IS NULL). Deleting
its number_giveaways row is enough on its own -- draw_card_batch_giveaway() reads its
card pool live from that table (batch_id + drawn_at IS NULL), it never uses
total_cards to decide what to draw -- and the same delete also un-locks the card for
its owner immediately (every "is this card busy in a giveaway" check across the
codebase queries number_giveaways for an undrawn row, so once this row is gone the
card is free to sell/stake/etc again). total_cards is decremented too, purely so the
stored count stays accurate (it's cosmetic/announcement-only, never read by the draw).

Run once from /root/peeppo on the server: python3 remove_giveaway_card_16.py
"""
import sqlite3

conn = sqlite3.connect("peeppo.db")
conn.row_factory = sqlite3.Row

row = conn.execute(
    "SELECT * FROM number_giveaways WHERE id = 322 AND batch_id = 2 AND user_card_id = 109 AND drawn_at IS NULL"
).fetchone()
if row is None:
    print("Row not found as expected -- nothing changed, check manually.")
else:
    conn.execute("DELETE FROM number_giveaways WHERE id = 322")
    conn.execute("UPDATE card_batch_giveaways SET total_cards = total_cards - 1 WHERE id = 2")
    conn.commit()
    new_total = conn.execute("SELECT total_cards FROM card_batch_giveaways WHERE id = 2").fetchone()[0]
    print(f"Removed giveaway_id=322 (Cybertruck, user_card_id=109, number 16). Batch #2 total_cards now {new_total}.")

conn.close()
