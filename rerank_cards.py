import sqlite3, random, shutil, sys, time, os, json
DB = "/root/peeppo/peeppo.db"
APPLY = "--apply" in sys.argv
CHANGES = {  # key -> new rarity
    "mma_belt": "diamond", "burj_khalifa": "platinum", "burj_al_arab": "platinum",
    "cannes_palme": "diamond", "j._p._morgan": "diamond", "stratus_rewards": "diamond",
    "coutts_world": "diamond", "davinci_trophy": "diamond", "diamond_black": "diamond",
    "diamond_red": "diamond",
}
if APPLY:
    os.makedirs("/root/peeppo/backups", exist_ok=True)
    bk = f"/root/peeppo/backups/peeppo_pre_rerank_{time.strftime('%Y%m%d_%H%M%S')}.db"
    src = sqlite3.connect(DB); dst = sqlite3.connect(bk); src.backup(dst); dst.close(); src.close()
    print("backup ->", bk)
c = sqlite3.connect(DB, timeout=60); c.row_factory = sqlite3.Row
c.execute("BEGIN IMMEDIATE")
files = {k: f"../nft/{k}.jpg" for k in CHANGES}
rows = {k: c.execute("SELECT id, rarity, name FROM cards WHERE filename=?", (f,)).fetchone() for k, f in files.items()}
missing = [k for k, r in rows.items() if r is None]
assert not missing, missing
changing_ids = {r["id"] for r in rows.values()}
report = {"cards": {}, "swapped_total": 0, "users": {}}
for k, new_r in CHANGES.items():
    r = rows[k]; old_r = r["rarity"]
    entry = {"name": r["name"], "old": old_r, "new": new_r, "owned_rows": 0}
    if old_r == new_r:
        report["cards"][k] = entry; continue
    pool = [x["id"] for x in c.execute("SELECT id FROM cards WHERE is_active=1 AND rarity=?", (old_r,)) if x["id"] not in changing_ids]
    assert pool, f"no replacement pool for {old_r}"
    owned = c.execute("SELECT id, user_id FROM user_cards WHERE card_id=?", (r["id"],)).fetchall()
    entry["owned_rows"] = len(owned)
    for o in owned:
        repl = random.choice(pool)
        c.execute("UPDATE user_cards SET card_id=? WHERE id=?", (repl, o["id"]))
        report["users"][str(o["user_id"])] = report["users"].get(str(o["user_id"]), 0) + 1
    report["swapped_total"] += len(owned)
    c.execute("UPDATE cards SET rarity=? WHERE id=?", (new_r, r["id"]))
    report["cards"][k] = entry
report["rarity_totals_now"] = [tuple(x) for x in c.execute("SELECT rarity, COUNT(*) FROM cards WHERE is_active=1 GROUP BY rarity")]
print(json.dumps(report, ensure_ascii=False, indent=1))
if APPLY:
    c.commit(); print("APPLIED")
else:
    c.rollback(); print("DRY RUN - nothing changed. Run with --apply")
