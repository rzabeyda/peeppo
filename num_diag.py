import sqlite3
c = sqlite3.connect("/root/peeppo/peeppo.db"); c.row_factory = sqlite3.Row
cnt = c.execute("SELECT COUNT(*) FROM user_cards").fetchone()[0]
print("rows:", cnt, "| max id:", c.execute("SELECT MAX(id) FROM user_cards").fetchone()[0])
print("== card_numbers status counts ==")
for r in c.execute("SELECT status, COUNT(*) n, MIN(number) mn, MAX(number) mx FROM card_numbers GROUP BY status"):
    print(tuple(r))
print("== owned/auction numbers above row count ==")
for r in c.execute("SELECT * FROM card_numbers WHERE status IN ('owned','auction') AND number > ? ORDER BY number", (cnt,)):
    print(dict(r))
print("== names of renamed cards ==")
for r in c.execute("SELECT filename, name, rarity, is_active FROM cards WHERE filename IN ('../nft/youtube_company.jpg','../nft/hermes_constance.jpg','../nft/basil_cathed.jpg','../nft/goldmund_reference.jpg')"):
    print(tuple(r))
print("active:", [tuple(r) for r in c.execute("SELECT rarity, COUNT(*) FROM cards WHERE is_active=1 GROUP BY rarity")])
