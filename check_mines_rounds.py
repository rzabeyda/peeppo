import sqlite3, json

conn = sqlite3.connect("/root/peeppo/peeppo.db")
conn.row_factory = sqlite3.Row

MINES_GRID_TILES = 25
MINES_RTP = 0.97

def mines_multiplier(mine_count, revealed_count):
    if revealed_count <= 0:
        return 1.0
    n = MINES_GRID_TILES
    fair = 1.0
    for i in range(revealed_count):
        fair *= (n - i) / (n - mine_count - i)
    return int(fair * MINES_RTP * 100) / 100

rows = conn.execute(
    "SELECT id, bet, mine_count, revealed, status, cashout_multiplier, payout FROM mines_rounds "
    "WHERE status IN ('won','lost') ORDER BY id"
).fetchall()

print(f"{'id':>4} {'bet':>5} {'mines':>5} {'revealed':>8} {'status':>5} {'mult_db':>8} {'mult_recalc':>11} {'payout':>7} {'expected_payout':>15} {'OK':>4}")
for r in rows:
    revealed = json.loads(r["revealed"])
    k = len(revealed)
    recalc = mines_multiplier(r["mine_count"], k) if r["status"] == "won" else None
    expected_payout = round(r["bet"] * recalc) if recalc is not None else 0
    ok = "OK"
    if r["status"] == "won":
        if r["cashout_multiplier"] is None or abs(r["cashout_multiplier"] - recalc) > 0.001:
            ok = "MULT_MISMATCH"
        if r["payout"] != expected_payout:
            ok = "PAYOUT_MISMATCH"
    else:
        if r["payout"] not in (0, None):
            ok = "LOSS_NONZERO_PAYOUT"
    print(f"{r['id']:>4} {r['bet']:>5} {r['mine_count']:>5} {k:>8} {r['status']:>5} {str(r['cashout_multiplier']):>8} {str(recalc):>11} {str(r['payout']):>7} {expected_payout:>15} {ok:>4}")
