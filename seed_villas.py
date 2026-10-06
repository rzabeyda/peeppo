"""Кладёт серию Stars ("Виллы") в БД из villa_cards.py. Идемпотентно (ищет по имени файла):
  - новые виллы вставляются со is_active=0 (в фарм/кейсы/крафт/эволюцию они не попадают)
  - у существующих обновляются имя и тир (если поменял в villa_cards.py), владельцы не трогаются
  - вилл, которых нет в списке, не удаляет и не трогает
  python3 seed_villas.py          - сухой прогон
  python3 seed_villas.py --apply  - применить (с бэкапом БД)"""
import sqlite3, sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from villa_cards import VILLA_CARDS
DB = "/root/peeppo/peeppo.db"
APPLY = "--apply" in sys.argv
if APPLY:
    os.makedirs("/root/peeppo/backups", exist_ok=True)
    bk = f"/root/peeppo/backups/peeppo_pre_villas_{time.strftime('%Y%m%d_%H%M%S')}.db"
    s = sqlite3.connect(DB); d = sqlite3.connect(bk); s.backup(d); d.close(); s.close()
    print("backup ->", bk)
c = sqlite3.connect(DB, timeout=60); c.row_factory = sqlite3.Row
cols = {r["name"] for r in c.execute("PRAGMA table_info(cards)")}
for col, ddl in (("series", "ALTER TABLE cards ADD COLUMN series TEXT"),
                 ("villa_number", "ALTER TABLE cards ADD COLUMN villa_number INTEGER")):
    if col not in cols:
        print("add column", col)
        if APPLY:
            c.execute(ddl)
cols = {r["name"] for r in c.execute("PRAGMA table_info(cards)")}
has_series = "series" in cols
added = renamed = retier = 0
now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
for key, name, tier in VILLA_CARDS:
    fn = f"../villa/{key}.jpg"
    row = c.execute("SELECT * FROM cards WHERE filename = ?", (fn,)).fetchone()
    if row is None:
        added += 1
        if APPLY:
            c.execute("INSERT INTO cards (filename, name, rarity, is_active, created_at, series) VALUES (?, ?, ?, 0, ?, 'villa')",
                      (fn, name, tier, now))
        continue
    if row["name"] != name:
        renamed += 1
        if APPLY: c.execute("UPDATE cards SET name = ? WHERE id = ?", (name, row["id"]))
    if row["rarity"] != tier:
        retier += 1
        if APPLY: c.execute("UPDATE cards SET rarity = ? WHERE id = ?", (tier, row["id"]))
    if APPLY and (not has_series or row["series"] != "villa" or row["is_active"]):
        c.execute("UPDATE cards SET series = 'villa', is_active = 0 WHERE id = ?", (row["id"],))
if APPLY:
    c.commit()
print(f"added {added}, renamed {renamed}, retier {retier}  ({'APPLIED' if APPLY else 'dry-run'})")
if has_series:
    for r in c.execute("SELECT rarity, COUNT(*) n, SUM(villa_number IS NOT NULL) sold FROM cards WHERE series='villa' GROUP BY rarity"):
        print(" ", r["rarity"], r["n"], "sold", r["sold"])
