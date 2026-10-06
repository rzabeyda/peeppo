"""Только чтение. Разбор одного игрока: откуда гемы и карты, особенно Diamond.
  python3 audit_user.py 6801225851"""
import sqlite3, sys, collections
uid = int(sys.argv[1])
c = sqlite3.connect("file:/root/peeppo/peeppo.db?mode=ro", uri=True); c.row_factory = sqlite3.Row
def q(sql, *a):
    try: return c.execute(sql, a).fetchall()
    except Exception as e: print("  (ошибка запроса:", str(e)[:80], ")"); return []
def one(sql, *a):
    r = q(sql, *a); return r[0] if r else None

u = one("select * from users where telegram_id=?", uid)
if not u: print("игрок не найден"); sys.exit()
print(f"== @{u['username']} ({u['first_name']}) id {uid}")
print(f"зарегистрирован: {u['created_at']}  последний вход: {u['last_seen_at']}  стрик: {u['streak_days']}")
print(f"гемов сейчас: {u['gems']}   всего заработано (gems_earned): {u['gems_earned']}")
refs = q("select telegram_id, username, created_at from users where ref_by=?", uid)
print(f"привёл рефералов: {len(refs)}   сам пришёл от: {u['ref_by']}")
st = one("select coalesce(sum(stars),0) s, count(*) n from stars_payments where user_id=?", uid)
print(f"купил гемов за Stars: {st['s']} ⭐ ({st['n']} платежей)")
dr = one("select count(*) n, coalesce(sum(amount),0) s from gem_drops where claimed_by=?", uid)
print(f"забрал дропов: {dr['n']} на {dr['s']} гемов")

print("\n-- Вывод Stars")
for w in q("select id, card_count, gram_amount, status, created_at from crypto_withdrawals where user_id=? order by id", uid):
    print(f"  #{w['id']} карт {w['card_count']} → {w['gram_amount']} Stars  {w['status']}  {w['created_at']}")

print("\n-- Карты сейчас (не сожжённые)")
for r in q("select k.rarity, count(*) n from user_cards uc join cards k on k.id=uc.card_id where uc.user_id=? and uc.voided=0 group by 1", uid):
    print(f"  {r['rarity']}: {r['n']}")
print("-- Сожжено/использовано (voided), по рангу:")
for r in q("select k.rarity, count(*) n from user_cards uc join cards k on k.id=uc.card_id where uc.user_id=? and uc.voided=1 group by 1", uid):
    print(f"  {r['rarity']}: {r['n']}")

print("\n-- Когда получены карты (по дням, по рангу; включая сожжённые)")
days = collections.defaultdict(lambda: collections.Counter())
for r in q("select substr(uc.obtained_at,1,10) d, k.rarity, count(*) n from user_cards uc join cards k on k.id=uc.card_id where uc.user_id=? group by 1,2", uid):
    days[r['d']][r['rarity']] += r['n']
for d in sorted(days)[-14:]:
    print(f"  {d}: " + ", ".join(f"{k} {v}" for k, v in days[d].items()))

print("\n-- Игры (раундов / поставил / получил / итог)")
games = {
 "Red&Black": "select count(*) n, coalesce(sum(bet),0) b, coalesce(sum(payout),0) p from redblack_rounds where user_id=?",
 "Mines": "select count(*) n, coalesce(sum(bet),0) b, coalesce(sum(payout),0) p from mines_rounds where user_id=? and status in ('won','lost')",
 "Aviator": "select count(*) n, coalesce(sum(bet),0) b, coalesce(sum(case when status='won' then cast(round(bet*cashout_multiplier) as integer) else 0 end),0) p from aviator_rounds where user_id=? and status in ('won','lost')",
 "Plinko": "select count(*) n, coalesce(sum(bet),0) b, coalesce(sum(payout),0) p from plinko_rounds where user_id=?",
 "Poker": "select count(*) n, coalesce(sum(bet),0) b, coalesce(sum(current_payout),0) p from poker_rounds where user_id=? and status in ('collected','busted','lost')",
}
tot = 0
for name, sql in games.items():
    r = one(sql, uid)
    if r: print(f"  {name:<10} {r['n']:>6} / {r['b']:>8} / {r['p']:>8} / {r['p']-r['b']:>+9}"); tot += r['p'] - r['b']
print(f"  Итого от казино: {tot:+d} гемов")

print("\n-- Самые крупные выигрыши (одним раундом)")
big = []
for name, sql in {
 "Mines": "select payout-bet w, bet, created_at from mines_rounds where user_id=? and status='won' order by payout-bet desc limit 3",
 "Plinko": "select payout-bet w, bet, created_at from plinko_rounds where user_id=? order by payout-bet desc limit 3",
 "Aviator": "select cast(round(bet*cashout_multiplier) as integer)-bet w, bet, created_at from aviator_rounds where user_id=? and status='won' order by w desc limit 3",
 "Poker": "select current_payout-bet w, bet, created_at from poker_rounds where user_id=? order by w desc limit 3",
 "Red&Black": "select payout-bet w, bet, created_at from redblack_rounds where user_id=? order by w desc limit 3"}.items():
    for r in q(sql, uid): big.append((r['w'], name, r['bet'], r['created_at']))
for w, name, b, t in sorted(big, reverse=True)[:6]:
    print(f"  {name}: +{w} при ставке {b}  ({t[:16]})")

print("\n-- Сделки (рынок/обмен), принятые")
try:
    for r in q("select seller_id, count(*) n, sum(price_gems) s from market_offers where status='accepted' and buyer_id=? group by seller_id order by n desc limit 5", uid):
        print(f"  купил у {r['seller_id']}: {r['n']} шт на {r['s']} гемов")
    for r in q("select buyer_id, count(*) n, sum(price_gems) s from market_offers where status='accepted' and seller_id=? group by buyer_id order by n desc limit 5", uid):
        print(f"  продал {r['buyer_id']}: {r['n']} шт на {r['s']} гемов")
except Exception as e: print("  нет данных", e)
print("  обменов принято (как получатель/как отправитель):",
      one("select count(*) n from swap_offers where status='accepted' and buyer_id=?", uid)['n'], "/",
      one("select count(*) n from swap_offers where status='accepted' and seller_id=?", uid)['n'])

print("\n-- Рефералы (когда пришли)")
for r in refs[:10]:
    print(f"  {r['telegram_id']} @{r['username']}  {r['created_at'][:16]}")
