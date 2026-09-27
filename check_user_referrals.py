import sys
import sqlite3
import database as db

USERNAME = "APESTOVAN_ZA_NARK0TY"

conn = sqlite3.connect("peeppo.db")
conn.row_factory = sqlite3.Row

me = conn.execute(
    "SELECT telegram_id, username, first_name, gems, gems_earned FROM users WHERE username = ? COLLATE NOCASE",
    (USERNAME,),
).fetchone()

if me is None:
    print(f"no user found with username @{USERNAME}")
    sys.exit(0)

print("=== user ===")
print(dict(me))
uid = me["telegram_id"]

print()
print("=== their referrals (people who signed up via their link), oldest first ===")
refs = conn.execute(
    "SELECT telegram_id, username, first_name, ref_reward_pending, chat_member_verified, created_at "
    "FROM users WHERE ref_by = ? ORDER BY created_at ASC",
    (uid,),
).fetchall()

if not refs:
    print("(no referrals at all)")
else:
    total_owed_pending = 0
    total_paid = 0
    for i, r in enumerate(refs):
        expected_reward = db._referral_reward_for_position(i)
        name = f"@{r['username']}" if r["username"] else (r["first_name"] or f"id{r['telegram_id']}")
        if r["chat_member_verified"] and r["ref_reward_pending"] == 0 and i < db.MAX_REWARDED_REFERRALS:
            status = f"PAID (position {i}, was worth {expected_reward} gems)"
            total_paid += expected_reward
        elif r["ref_reward_pending"] > 0:
            reason = "waiting for chat join" if not r["chat_member_verified"] else "queued, not yet resolved (shouldn't happen)"
            status = f"PENDING -- {reason} (position {i}, {r['ref_reward_pending']} gems queued)"
            total_owed_pending += r["ref_reward_pending"]
        elif i >= db.MAX_REWARDED_REFERRALS:
            status = f"past the {db.MAX_REWARDED_REFERRALS}-referral cap, no reward (position {i})"
        else:
            status = f"no reward queued yet -- hasn't farmed a first card yet (position {i})"
        print(f"  {name} (id={r['telegram_id']}): {status}")
    print()
    print(f"total referrals: {len(refs)}")
    print(f"total gems PAID so far from referrals: {total_paid}")
    print(f"total gems still PENDING (queued, waiting on chat join): {total_owed_pending}")
