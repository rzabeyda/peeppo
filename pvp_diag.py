import sqlite3
c = sqlite3.connect("/root/peeppo/peeppo.db")
c.row_factory = sqlite3.Row
print("== PvP rounds (not resolved) ==")
for r in c.execute("SELECT * FROM pvp_rounds WHERE status != 'resolved'"):
    print(dict(r))
print("== entries of open rounds ==")
for r in c.execute("""SELECT e.id eid, e.round_id, e.user_id, u.username, e.user_card_id, e.rarity,
        uc.voided, uc.custom_name, cd.filename, cd.name
    FROM pvp_entries e JOIN pvp_rounds p ON p.id=e.round_id
    LEFT JOIN users u ON u.telegram_id=e.user_id
    LEFT JOIN user_cards uc ON uc.id=e.user_card_id
    LEFT JOIN cards cd ON cd.id=uc.card_id
    WHERE p.status != 'resolved'"""):
    print(dict(r))
print("== user_cards with pvp_round_id set ==")
for r in c.execute("""SELECT uc.id, uc.user_id, u.username, uc.pvp_round_id, uc.voided, uc.custom_name, cd.rarity, cd.filename
    FROM user_cards uc JOIN cards cd ON cd.id=uc.card_id LEFT JOIN users u ON u.telegram_id=uc.user_id
    WHERE uc.pvp_round_id IS NOT NULL"""):
    print(dict(r))
print("== live non-obsidian OLD cards left (should be 0) ==")
print(c.execute("""SELECT COUNT(*) FROM user_cards uc JOIN cards cd ON cd.id=uc.card_id
    WHERE uc.voided=0 AND uc.custom_name IS NULL AND cd.filename NOT LIKE '../nft/%'""").fetchone()[0])
print("== live cards by user (top 10) ==")
for r in c.execute("""SELECT uc.user_id, u.username, COUNT(*) n FROM user_cards uc LEFT JOIN users u ON u.telegram_id=uc.user_id
    WHERE uc.voided=0 GROUP BY uc.user_id ORDER BY n DESC LIMIT 10"""):
    print(dict(r))
print("== latest pvp rounds ==")
for r in c.execute("SELECT * FROM pvp_rounds ORDER BY id DESC LIMIT 5"):
    print(dict(r))
