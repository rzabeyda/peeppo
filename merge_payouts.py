"""Склеивает две последние (самые новые) выплаты ОДНОГО игрока в «Истории выплат» в одну строку.
  python3 merge_payouts.py                - сухой прогон: покажет, какие строки склеит
  python3 merge_payouts.py --apply        - склеить (делает бэкап БД)
  python3 merge_payouts.py ID1 ID2 [...]  - явно указать id выплат (crypto_withdrawals.id)
Новая строка = более свежая выплата с суммой Stars и числом карт обеих; старая получает статус 'merged'
(в историю не попадает, в базе остаётся). Карты старой выплаты переезжают на новую."""
import sqlite3, sys, os, time
DB = os.environ.get("PEEPPO_DB", "/root/peeppo/peeppo.db")
args = [a for a in sys.argv[1:] if not a.startswith("--")]
APPLY = "--apply" in sys.argv
c = sqlite3.connect(DB, timeout=60); c.row_factory = sqlite3.Row
if args:
    ids = [int(a) for a in args]
    rows = [c.execute("SELECT * FROM crypto_withdrawals WHERE id=? AND status='paid'", (i,)).fetchone() for i in ids]
    assert all(rows), "не все id найдены среди выплаченных"
else:
    rows = c.execute("SELECT * FROM crypto_withdrawals WHERE status='paid' ORDER BY resolved_at DESC, id DESC LIMIT 2").fetchall()
assert len(rows) >= 2, "нужно минимум 2 выплаты"
assert len({r["user_id"] for r in rows}) == 1, "выплаты разных игроков — не склеиваю: " + str([(r['id'], r['user_id']) for r in rows])
rows = sorted(rows, key=lambda r: (r["resolved_at"] or "", r["id"]), reverse=True)
keep, drop = rows[0], rows[1:]
total_amount = sum(r["gram_amount"] for r in rows); total_cards = sum(r["card_count"] for r in rows)
u = c.execute("SELECT username, first_name FROM users WHERE telegram_id=?", (keep["user_id"],)).fetchone()
print("игрок:", u["username"] or u["first_name"])
for r in rows:
    print(f"  выплата #{r['id']}: {r['gram_amount']} Stars, {r['card_count']} карт, {r['resolved_at']}")
print(f"=> одна строка: #{keep['id']} = {total_amount} Stars, {total_cards} карт")
if APPLY:
    os.makedirs(os.path.join(os.path.dirname(DB), "backups"), exist_ok=True)
    bk = os.path.join(os.path.dirname(DB), "backups", f"peeppo_pre_merge_{time.strftime('%Y%m%d_%H%M%S')}.db")
    s = sqlite3.connect(DB); d = sqlite3.connect(bk); s.backup(d); d.close(); s.close(); print("backup ->", bk)
    c.execute("BEGIN IMMEDIATE")
    for r in drop:
        c.execute("UPDATE crypto_withdrawal_cards SET withdrawal_id=? WHERE withdrawal_id=?", (keep["id"], r["id"]))
        c.execute("UPDATE crypto_withdrawals SET status='merged' WHERE id=?", (r["id"],))
    c.execute("UPDATE crypto_withdrawals SET gram_amount=?, card_count=? WHERE id=?", (total_amount, total_cards, keep["id"]))
    c.commit(); print("APPLIED")
else:
    print("DRY RUN — ничего не изменено. Запусти с --apply")
