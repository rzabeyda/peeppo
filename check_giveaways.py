"""
One-off diagnostic: what giveaways are currently active right now, and does any of
them include a card whose number is 16 -- needed before acting on "убери из розыгрыша
карту номер 16" so we know exactly which row to touch instead of guessing.

Run once from /root/peeppo on the server: python3 check_giveaways.py
"""
import sqlite3

conn = sqlite3.connect("peeppo.db")
conn.row_factory = sqlite3.Row

print("=== active card_batch_giveaways (drawn_at IS NULL) ===")
batches = conn.execute("SELECT * FROM card_batch_giveaways WHERE drawn_at IS NULL").fetchall()
for b in batches:
    print(dict(b))

print()
print("=== active number_giveaways (drawn_at IS NULL) tied to those batches ===")
for b in batches:
    rows = conn.execute(
        """
        SELECT ng.id AS giveaway_id, ng.user_card_id, ng.number AS target_number, ng.draw_at,
               COALESCE(uc.number_override,
                   (SELECT COUNT(*) FROM user_cards uc2 WHERE uc2.obtained_at <= uc.obtained_at)) AS drop_number,
               c.name
        FROM number_giveaways ng
        JOIN user_cards uc ON uc.id = ng.user_card_id
        JOIN cards c ON c.id = uc.card_id
        WHERE ng.batch_id = ? AND ng.drawn_at IS NULL
        """,
        (b["id"],),
    ).fetchall()
    for r in rows:
        print(dict(r))

print()
print("=== standalone active number_giveaways (batch_id IS NULL) ===")
rows = conn.execute(
    """
    SELECT ng.id AS giveaway_id, ng.user_card_id, ng.number AS target_number, ng.draw_at,
           COALESCE(uc.number_override,
               (SELECT COUNT(*) FROM user_cards uc2 WHERE uc2.obtained_at <= uc.obtained_at)) AS drop_number,
           c.name
    FROM number_giveaways ng
    JOIN user_cards uc ON uc.id = ng.user_card_id
    JOIN cards c ON c.id = uc.card_id
    WHERE ng.batch_id IS NULL AND ng.drawn_at IS NULL
    """
).fetchall()
for r in rows:
    print(dict(r))

conn.close()
