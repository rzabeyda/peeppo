"""Восстанавливает счётчики /admin «с момента обновления NFT».
Берёт значения на момент миграции из бэкапа peeppo_pre_nft_migration_*.db, значения на момент обнуления
из action_counters_backup и прибавляет разницу (то, что набежало между миграцией и обнулением) к текущим.
  python3 restore_counters.py          - сухой прогон (покажет расчёт)
  python3 restore_counters.py --apply  - применить (один раз)"""
import sqlite3, sys, glob, os
DB = os.environ.get("PEEPPO_DB", "/root/peeppo/peeppo.db")
BK_DIR = os.path.join(os.path.dirname(DB), "backups")
APPLY = "--apply" in sys.argv
RESET = ("farm", "evolve", "craft", "case_open", "stake", "blind_swap_completed")
files = sorted(glob.glob(os.path.join(BK_DIR, "peeppo_pre_nft_migration_*.db")))
assert files, "не найден бэкап peeppo_pre_nft_migration_*.db"
bk = files[-1]
print("бэкап на момент миграции:", os.path.basename(bk), f"(всего таких: {len(files)})")
b = sqlite3.connect(bk)
base = dict(b.execute("SELECT action, count FROM action_counters").fetchall()); b.close()
c = sqlite3.connect(DB, timeout=60)
assert c.execute("SELECT 1 FROM sqlite_master WHERE name='action_counters_backup'").fetchone(), "нет action_counters_backup — обнуление не запускалось"
c.execute("CREATE TABLE IF NOT EXISTS counters_restored (at TEXT)")
assert not c.execute("SELECT 1 FROM counters_restored").fetchone(), "уже восстанавливал — повторно не буду (иначе удвоится)"
at_reset = {a: n for a, n in c.execute("SELECT action, MAX(count) FROM action_counters_backup GROUP BY action")}
cur = dict(c.execute("SELECT action, count FROM action_counters").fetchall())
print(f"{'действие':22} {'в миграцию':>10} {'при обнулении':>14} {'+набежало':>10} {'сейчас':>7} {'станет':>7}")
plan = {}
for a in RESET:
    since = max(0, at_reset.get(a, 0) - base.get(a, 0))
    plan[a] = cur.get(a, 0) + since
    print(f"{a:22} {base.get(a,0):>10} {at_reset.get(a,0):>14} {since:>10} {cur.get(a,0):>7} {plan[a]:>7}")
if APPLY:
    for a, n in plan.items():
        c.execute("INSERT INTO action_counters (action, count) VALUES (?, ?) ON CONFLICT(action) DO UPDATE SET count = excluded.count", (a, n))
    c.execute("INSERT INTO counters_restored VALUES (datetime('now'))")
    c.commit(); print("ВОССТАНОВЛЕНО")
else:
    print("DRY RUN — ничего не изменено. Запусти с --apply")
