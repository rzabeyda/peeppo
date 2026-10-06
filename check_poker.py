"""Только чтение. Разбирает реальную статистику Покера: сколько раундов, RTP, как часто выпадают
комбинации против ожидаемого, и играют ли люди по подсказке (держат ли собранную комбинацию).
  python3 check_poker.py"""
import sqlite3, json, math, collections, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import database as db
EXPECT = {"nothing": 69.92, "two_pair": 10.49, "three_kind": 12.80, "straight": 3.02, "flush": 1.48,
          "full_house": 1.41, "four_kind": 0.82, "straight_flush": 0.032, "royal_flush": 0.005, "five_of_a_kind": 0.0095}
c = sqlite3.connect("file:/root/peeppo/peeppo.db?mode=ro", uri=True); c.row_factory = sqlite3.Row
excl = ",".join("?" for _ in db.LEADERBOARD_EXCLUDED_USERNAMES)
rows = c.execute(
    "SELECT pr.* FROM poker_rounds pr JOIN users u ON u.telegram_id = pr.user_id "
    f"WHERE pr.status IN ('collected','busted','lost') AND LOWER(COALESCE(u.username,'')) NOT IN ({excl}) AND pr.created_at >= ?",
    list(db.LEADERBOARD_EXCLUDED_USERNAMES) + [db.POKER_STATS_RESET_AT]).fetchall()
n = len(rows)
if n == 0:
    print('раундов нет'); sys.exit()
bet = sum(r["bet"] for r in rows); paid = sum(r["current_payout"] for r in rows)
base = sum(r["base_payout"] or 0 for r in rows)
print(f"раундов: {n}  поставлено: {bet}  выплачено: {paid}  RTP: {paid/bet*100:.1f}%  (до гэмбла: {base/bet*100:.1f}%)")
res = [(r["current_payout"] / r["bet"]) for r in rows]
mean = sum(res) / n; sd = math.sqrt(sum((x - mean) ** 2 for x in res) / n)
print(f"разброс: ± {sd/math.sqrt(n)*100*1.96:.0f}% (95% интервал для RTP при честной игре)")
cats = collections.Counter(r["category"] or "nothing" for r in rows)
print(f"\n{'комбинация':<16}{'раз':>6}{'факт %':>9}{'ожид %':>9}")
for k, e in EXPECT.items():
    print(f"{k:<16}{cats.get(k,0):>6}{cats.get(k,0)/n*100:>9.2f}{e:>9.2f}")
broke = held_none = kept_pair_only = 0
for r in rows:
    dealt = json.loads(r["dealt_cards"]); hm = json.loads(r["held_mask"]) if r["held_mask"] else [False]*5
    dc = db._poker_evaluate(dealt)
    if dc in ("straight", "flush", "full_house", "four_kind", "straight_flush", "royal_flush", "five_of_a_kind") and not all(hm):
        broke += 1
    if not any(hm): held_none += 1
print(f"\nпришла готовая сильная рука (стрит и выше), а игрок часть карт сбросил: {broke}")
print(f"игрок не держал ни одной карты: {held_none} ({held_none/n*100:.0f}%)")
by_user = collections.defaultdict(lambda: [0, 0, 0])
for r in rows:
    u = by_user[r["user_id"]]; u[0] += 1; u[1] += r["bet"]; u[2] += r["current_payout"]
print("\nтоп игроков по числу раундов (раундов / поставил / получил):")
for uid, (k, b, p) in sorted(by_user.items(), key=lambda kv: -kv[1][0])[:8]:
    print(f"  {uid}: {k} / {b} / {p}  RTP {p/b*100:.0f}%")
