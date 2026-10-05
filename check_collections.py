"""Только чтение. Показывает по каждой коллекции: сколько карт, сколько записей о завершении,
сколько игроков реально имеют ВСЕ карты, и сколько имеют все кроме одной / хотя бы половину.
  python3 check_collections.py"""
import sqlite3
c = sqlite3.connect("file:/root/peeppo/peeppo.db?mode=ro", uri=True)
print("users:", c.execute("select count(*) from users").fetchone()[0])
print(f"{'id':>3} {'collection':<16} {'cards':>5} {'записей':>8} {'все карты':>9} {'-1 карта':>8} {'>=половины':>10}")
for cid, name in c.execute("select id, name from collections order by id").fetchall():
    tot = c.execute("select count(*) from collection_cards where collection_id=?", (cid,)).fetchone()[0]
    rec = c.execute("select count(*) from user_collection_completions where collection_id=?", (cid,)).fetchone()[0]
    per = [r[0] for r in c.execute(
        "select count(distinct cc.card_id) from collection_cards cc join user_cards uc on uc.card_id=cc.card_id and uc.voided=0 "
        "where cc.collection_id=? group by uc.user_id", (cid,))]
    full = sum(1 for n in per if n >= tot)
    minus1 = sum(1 for n in per if n == tot - 1)
    half = sum(1 for n in per if n * 2 >= tot)
    print(f"{cid:>3} {name:<16} {tot:>5} {rec:>8} {full:>9} {minus1:>8} {half:>10}")
