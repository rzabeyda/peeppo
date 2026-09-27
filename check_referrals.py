import sqlite3

conn = sqlite3.connect("peeppo.db")
conn.row_factory = sqlite3.Row

print("=== referral reward schedule sanity ===")
import database as db
for pos in range(0, 22):
    print(pos, "->", db._referral_reward_for_position(pos))

print()
print("=== users with ref_by set (all referred users) ===")
rows = conn.execute(
    "SELECT telegram_id, username, first_name, ref_by, ref_reward_pending, chat_member_verified, created_at "
    "FROM users WHERE ref_by IS NOT NULL ORDER BY created_at DESC LIMIT 40"
).fetchall()
for r in rows:
    print(dict(r))

print()
print("=== counts ===")
total = conn.execute("SELECT COUNT(*) n FROM users WHERE ref_by IS NOT NULL").fetchone()["n"]
verified = conn.execute("SELECT COUNT(*) n FROM users WHERE ref_by IS NOT NULL AND chat_member_verified=1").fetchone()["n"]
pending_unverified = conn.execute(
    "SELECT COUNT(*) n FROM users WHERE ref_by IS NOT NULL AND chat_member_verified=0 AND ref_reward_pending>0"
).fetchone()["n"]
paid_out = conn.execute(
    "SELECT COUNT(*) n FROM users WHERE ref_by IS NOT NULL AND chat_member_verified=1 AND ref_reward_pending=0"
).fetchone()["n"]
never_farmed_no_pending = conn.execute(
    "SELECT COUNT(*) n FROM users WHERE ref_by IS NOT NULL AND ref_reward_pending=0 AND chat_member_verified=0"
).fetchone()["n"]
print("total referred users:", total)
print("chat_member_verified=1:", verified)
print("waiting on chat join (farmed, pending gems queued):", pending_unverified)
print("verified + reward already paid (pending=0):", paid_out)
print("never farmed yet (pending=0, not verified) -- normal, no card farmed so no reward queued:", never_farmed_no_pending)

print()
print("=== top referrers: how many rewarded referrals have they actually received gems for? ===")
top = conn.execute(
    "SELECT ref_by, COUNT(*) n FROM users WHERE ref_by IS NOT NULL GROUP BY ref_by ORDER BY n DESC LIMIT 10"
).fetchall()
for t in top:
    referrer_id = t["ref_by"]
    referrer = conn.execute("SELECT username, first_name, gems, gems_earned FROM users WHERE telegram_id=?", (referrer_id,)).fetchone()
    paid = conn.execute(
        "SELECT COUNT(*) n FROM users WHERE ref_by=? AND chat_member_verified=1 AND ref_reward_pending=0", (referrer_id,)
    ).fetchone()["n"]
    print(f"referrer {referrer_id} ({referrer['username'] or referrer['first_name']}): {t['n']} total referrals, {paid} paid-out, gems={referrer['gems']} gems_earned={referrer['gems_earned']}")
