"""Синхронизирует каталог БД с new_cards.py (источник правды): добавляет новые карты, правит ранги. Идемпотентно: меняет только то,
что отличается. Владельцам карты, у которой меняется ранг, выдаётся ДРУГАЯ карта их прежнего
ранга (карта в user_cards заменяется на месте, номер/место сохраняются).
  python3 rerank_cards.py          - сухой прогон (ничего не меняет)
  python3 rerank_cards.py --apply  - применить (перед этим делает бэкап БД)"""
import sqlite3, random, sys, time, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from new_cards import NEW_CARDS
DB = "/root/peeppo/peeppo.db"
APPLY = "--apply" in sys.argv
TARGET = {f"../nft/{k}.jpg": (name, r) for k, name, r in NEW_CARDS}
# Карты, которые убрали из каталога: владельцам выдаётся ДРУГАЯ карта того же ранга, потом карта
# выключается (is_active=0). Если владельцев нет - просто выключается. Идемпотентно.
RETIRE = ["../nft/codex_leicester.jpg"]
if APPLY:
    os.makedirs("/root/peeppo/backups", exist_ok=True)
    bk = f"/root/peeppo/backups/peeppo_pre_rerank_{time.strftime('%Y%m%d_%H%M%S')}.db"
    src = sqlite3.connect(DB); dst = sqlite3.connect(bk); src.backup(dst); dst.close(); src.close()
    print("backup ->", bk)
c = sqlite3.connect(DB, timeout=60); c.row_factory = sqlite3.Row
c.execute("BEGIN IMMEDIATE")
# сначала добавляем карты, которых ещё нет в каталоге (новые файлы в static/nft)
added = []
for f, (name, r_) in TARGET.items():
    if not c.execute("SELECT 1 FROM cards WHERE filename=?", (f,)).fetchone():
        c.execute("INSERT INTO cards (filename, name, rarity, is_active, created_at) VALUES (?, ?, ?, 1, ?)",
                  (f, name, r_, time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())))
        added.append(f[7:-4])
    else:
        c.execute("UPDATE cards SET name=? WHERE filename=? AND name != ?", (name, f, name))
retired = []
for f in RETIRE:
    old = c.execute("SELECT id, rarity FROM cards WHERE filename=? AND is_active=1", (f,)).fetchone()
    if not old:
        continue
    pool_r = [x["id"] for x in c.execute(
        "SELECT id FROM cards WHERE is_active=1 AND rarity=? AND id != ? AND filename IN (%s)" % ",".join("?" * len(TARGET)),
        (old["rarity"], old["id"], *TARGET))]
    assert pool_r, f"no replacement pool for {old['rarity']}"
    have_by = {}
    for x in c.execute("SELECT user_id, card_id FROM user_cards WHERE voided=0"):
        have_by.setdefault(x["user_id"], set()).add(x["card_id"])
    n = 0
    for o in c.execute("SELECT id, user_id FROM user_cards WHERE card_id=?", (old["id"],)).fetchall():
        have = have_by.setdefault(o["user_id"], set())
        fresh = [x for x in pool_r if x not in have]
        repl = random.choice(fresh or pool_r)
        c.execute("UPDATE user_cards SET card_id=? WHERE id=?", (repl, o["id"]))
        have.add(repl); n += 1
    c.execute("DELETE FROM collection_cards WHERE card_id=?", (old["id"],))
    c.execute("UPDATE cards SET is_active=0 WHERE id=?", (old["id"],))  # владельцев уже нет -> компенсация 0
    retired.append({"card": f[7:-4], "owner_rows_swapped": n})
rows = {r["filename"]: r for r in c.execute("SELECT id, filename, rarity, name FROM cards WHERE is_active=1")}
missing = [f for f in TARGET if f not in rows]
assert not missing, missing
changing = {f: (rows[f]["rarity"], TARGET[f][1]) for f in TARGET if rows[f]["rarity"] != TARGET[f][1]}
changing_ids = {rows[f]["id"] for f in changing}
pool_by_rarity = {}
for r in rows.values():
    if r["id"] not in changing_ids:
        pool_by_rarity.setdefault(r["rarity"], []).append(r["id"])
pending = {x[0] for x in c.execute(
    "SELECT wc.user_card_id FROM crypto_withdrawal_cards wc JOIN crypto_withdrawals w ON w.id=wc.withdrawal_id WHERE w.status='pending'")}
owned_by_user = {}
for x in c.execute("SELECT user_id, card_id FROM user_cards WHERE voided=0"):
    owned_by_user.setdefault(x["user_id"], set()).add(x["card_id"])
report = {"retired": retired, "cards_added": added, "cards": {}, "swapped_total": 0, "users": {}}
for f, (old_r, new_r) in sorted(changing.items(), key=lambda kv: kv[0]):
    r = rows[f]
    pool = pool_by_rarity.get(old_r)
    assert pool, f"no replacement pool for {old_r}"
    owned = [o for o in c.execute("SELECT id, user_id, voided FROM user_cards WHERE card_id=?", (r["id"],))
             if o["voided"] == 0 or o["id"] in pending]
    report["cards"][f[7:-4]] = {"old": old_r, "new": new_r, "owned_rows": len(owned)}
    for o in owned:
        have = owned_by_user.setdefault(o["user_id"], set())
        fresh = [x for x in pool if x not in have]
        repl = random.choice(fresh or pool)
        c.execute("UPDATE user_cards SET card_id=? WHERE id=?", (repl, o["id"]))
        have.add(repl)
        report["users"][str(o["user_id"])] = report["users"].get(str(o["user_id"]), 0) + 1
    report["swapped_total"] += len(owned)
    c.execute("UPDATE cards SET rarity=? WHERE id=?", (new_r, r["id"]))
report["cards_changed"] = len(changing)
report["rarity_totals_now"] = [tuple(x) for x in c.execute("SELECT rarity, COUNT(*) FROM cards WHERE is_active=1 GROUP BY rarity")]
print(json.dumps(report, ensure_ascii=False, indent=1))
if APPLY:
    c.commit(); print("APPLIED")
else:
    c.rollback(); print("DRY RUN - nothing changed. Run with --apply")
