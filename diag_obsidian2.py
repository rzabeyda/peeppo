"""Read-only: checks that Obsidian card (user_card id given) is returned by the same
function the profile uses, and that its image file exists. Run in /root/peeppo."""
import os, sys
import database as db

uc_id = int(sys.argv[1]) if len(sys.argv) > 1 else 4978
with db.get_conn() as c:
    row = c.execute(
        "SELECT uc.user_id, uc.pinned_at, uc.number_override, c.id AS card_id, c.filename, c.name, c.is_active, c.rarity "
        "FROM user_cards uc JOIN cards c ON c.id = uc.card_id WHERE uc.id = ?", (uc_id,)
    ).fetchone()
print("card row:", dict(row))
path = os.path.join("static", "cards", row["filename"])
print("image file:", path, "EXISTS" if os.path.exists(path) else "MISSING")
inv = db.get_inventory(row["user_id"])
print("inventory size:", len(inv))
mine = [x for x in inv if x["user_card_id"] == uc_id]
print("in get_inventory():", mine if mine else "NOT FOUND")
print("obsidian cards in this inventory:", sum(1 for x in inv if x["custom_name"]))
