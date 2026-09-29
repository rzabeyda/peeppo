import sys
sys.path.insert(0, "/root/peeppo")
import database as db

UID = 5392127313

with db.get_conn() as conn:
    total_rounds = conn.execute("SELECT COUNT(*) AS n FROM pvp_rounds WHERE status='resolved'").fetchone()["n"]
    print("total resolved PvP rounds in whole game:", total_rounds)

    entries = conn.execute(
        "SELECT COUNT(DISTINCT round_id) AS n FROM pvp_entries WHERE user_id = ?", (UID,)
    ).fetchone()["n"]
    print("rounds this user PARTICIPATED in:", entries)

    wins = conn.execute("SELECT COUNT(*) AS n FROM pvp_rounds WHERE winner_id = ?", (UID,)).fetchone()["n"]
    print("rounds this user WON:", wins)

    # per-round detail: their weight vs total weight, and who else was in each won round
    rows = conn.execute(
        "SELECT pr.id, pr.created_at, pr.resolved_at, "
        "(SELECT SUM(weight) FROM pvp_entries WHERE round_id = pr.id AND user_id = ?) AS my_weight, "
        "(SELECT SUM(weight) FROM pvp_entries WHERE round_id = pr.id) AS total_weight, "
        "(SELECT COUNT(DISTINCT user_id) FROM pvp_entries WHERE round_id = pr.id) AS n_players "
        "FROM pvp_rounds pr WHERE pr.winner_id = ? ORDER BY pr.id",
        (UID, UID)
    ).fetchall()
    print(f"\nDetail of {len(rows)} won rounds (my_weight/total_weight, n_players):")
    for r in rows:
        d = dict(r)
        pct = round(100 * d['my_weight'] / d['total_weight'], 1) if d['total_weight'] else None
        print(f"  round {d['id']} resolved {d['resolved_at']}: my_weight={d['my_weight']} total={d['total_weight']} chance={pct}% players={d['n_players']}")

    # opponents faced across all their rounds (won or lost) -- to check for repeat alt accounts
    opponents = conn.execute(
        "SELECT pe.user_id, u.username, u.first_name, u.created_at, COUNT(DISTINCT pe.round_id) AS rounds_together "
        "FROM pvp_entries pe JOIN users u ON u.telegram_id = pe.user_id "
        "WHERE pe.user_id != ? AND pe.round_id IN (SELECT round_id FROM pvp_entries WHERE user_id = ?) "
        "GROUP BY pe.user_id ORDER BY rounds_together DESC",
        (UID, UID)
    ).fetchall()
    print(f"\nOpponents faced ({len(opponents)} distinct):")
    for r in opponents:
        print(" ", dict(r))
