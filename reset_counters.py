"""Обнуляет счётчики действий в /admin (Фарм, Эволюция, Крафт, Кейсы, Стейки, Обмен).
Юзеры и «Сегодня» не трогаются — они считаются из таблицы users.
  python3 reset_counters.py          - показать текущие значения (сухой прогон)
  python3 reset_counters.py --apply  - обнулить (старые значения сохраняются в action_counters_backup)"""
import sqlite3, sys, time
DB = "/root/peeppo/peeppo.db"
RESET = ("farm", "evolve", "craft", "case_open", "stake", "blind_swap_completed")  # НЕ трогаем migration_new_cards — это защёлка миграции
Q = ",".join("?" for _ in RESET)
APPLY = "--apply" in sys.argv
c = sqlite3.connect(DB, timeout=60)
rows = c.execute(f"SELECT action, count FROM action_counters WHERE action IN ({Q}) ORDER BY action", RESET).fetchall()
for a, n in rows:
    print(f"  {a}: {n}")
if APPLY:
    c.execute("CREATE TABLE IF NOT EXISTS action_counters_backup (saved_at TEXT, action TEXT, count INTEGER)")
    ts = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    c.executemany("INSERT INTO action_counters_backup VALUES (?, ?, ?)", [(ts, a, n) for a, n in rows])
    c.execute(f"UPDATE action_counters SET count = 0 WHERE action IN ({Q})", RESET)
    c.commit()
    print("ОБНУЛЕНО (старые значения в action_counters_backup)")
else:
    print("DRY RUN — ничего не изменено. Запусти с --apply")
