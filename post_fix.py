import sqlite3, glob, sys
sys.path.insert(0, "/root/peeppo")
from new_cards import NEW_CARDS
PAY = {"bronze":25,"silver":50,"gold":150,"platinum":600,"diamond":3000}
c = sqlite3.connect("/root/peeppo/peeppo.db", timeout=30)
c.row_factory = sqlite3.Row
c.execute("BEGIN IMMEDIATE")

# 1) card names
n = 0
for key, name, rarity in NEW_CARDS:
    cur = c.execute("UPDATE cards SET name=? WHERE filename=? AND name<>?", (name, f"../nft/{key}.jpg", name))
    n += cur.rowcount
print("renamed cards:", n)

# 2) PvP leftovers: entries whose card is already voided
pre = sorted(glob.glob("/root/peeppo/backups/peeppo_pre_nft_migration_*.db"))[0]
old = sqlite3.connect(pre)
bad = c.execute("""SELECT e.id eid, e.round_id, e.user_id, e.user_card_id, e.rarity FROM pvp_entries e
    JOIN user_cards uc ON uc.id=e.user_card_id WHERE uc.voided=1
    AND e.round_id IN (SELECT id FROM pvp_rounds WHERE status!='resolved')""").fetchall()
print("stuck entries:", len(bad))
paid_in_migration = 0
for r in bad:
    was = old.execute("SELECT voided FROM user_cards WHERE id=?", (r["user_card_id"],)).fetchone()
    if was is not None and was[0] == 0:
        paid_in_migration += PAY.get(r["rarity"], 25)
print("these cards were live before migration -> already paid in migration, gems:", paid_in_migration)
rounds = set()
for r in bad:
    c.execute("DELETE FROM pvp_entries WHERE id=?", (r["eid"],))
    c.execute("UPDATE user_cards SET pvp_round_id=NULL WHERE id=?", (r["user_card_id"],))
    rounds.add(r["round_id"])
for rid in rounds:
    left = c.execute("SELECT COUNT(*) FROM pvp_entries WHERE round_id=?", (rid,)).fetchone()[0]
    if left == 0:
        c.execute("UPDATE user_cards SET pvp_round_id=NULL WHERE pvp_round_id=?", (rid,))
        c.execute("DELETE FROM pvp_rounds WHERE id=? AND status!='resolved'", (rid,))
        print("removed empty open round", rid)
print("voided cards still tied to PvP:", c.execute("SELECT COUNT(*) FROM user_cards WHERE voided=1 AND pvp_round_id IS NOT NULL").fetchone()[0])
c.commit()

# 3) numbering sanity
cnt = c.execute("SELECT COUNT(*) FROM user_cards").fetchone()[0]
mx_ov = c.execute("SELECT COALESCE(MAX(number_override),0) FROM user_cards WHERE voided=0").fetchone()[0]
mx_own = c.execute("SELECT COALESCE(MAX(number),0) FROM card_numbers WHERE status IN ('owned','auction')").fetchone()[0]
print("user_cards rows (next farm gets natural number):", cnt + 1)
print("max live pinned override:", mx_ov, "| max owned/auction number:", mx_own)
print("OK, no collision ahead" if max(mx_ov, mx_own) <= cnt else "WARNING: some owned number is above the row count")
dups = c.execute("""SELECT n, COUNT(*) FROM (SELECT COALESCE(number_override,(SELECT COUNT(*) FROM user_cards u2 WHERE u2.obtained_at<=uc.obtained_at)) n FROM user_cards uc WHERE voided=0) GROUP BY n HAVING COUNT(*)>1""").fetchall()
print("duplicate displayed numbers among live cards:", [tuple(d) for d in dups])
print("active cards:", [tuple(r) for r in c.execute("SELECT rarity, COUNT(*) FROM cards WHERE is_active=1 GROUP BY rarity")])
