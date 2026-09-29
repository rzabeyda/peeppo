import sys
sys.path.insert(0, "/root/peeppo")
import database as db

USERNAME = "fuckzxm"

with db.get_conn() as conn:
    user = conn.execute(
        "SELECT telegram_id, username, first_name, created_at, gems, gems_earned, ref_by "
        "FROM users WHERE LOWER(username) = ?", (USERNAME.lower(),)
    ).fetchone()
    if not user:
        print("user not found")
        sys.exit()
    uid = user["telegram_id"]
    print("USER:", dict(user))

    diamond_cards = conn.execute(
        "SELECT uc.id, uc.obtained_at, uc.voided, uc.listed_price, uc.swap_listed, "
        "uc.staked_at, uc.pvp_round_id, uc.pinned_at, c.name, c.filename "
        "FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
        "WHERE uc.user_id = ? AND c.rarity = 'diamond' ORDER BY uc.obtained_at",
        (uid,)
    ).fetchall()
    print(f"\nTOTAL diamond user_cards rows ever (incl. voided): {len(diamond_cards)}")
    for r in diamond_cards:
        print(dict(r))

    withdrawals = conn.execute(
        "SELECT id, card_count, gram_amount, wallet_address, status, created_at "
        "FROM crypto_withdrawals WHERE user_id = ? ORDER BY created_at",
        (uid,)
    ).fetchall()
    print(f"\nCRYPTO WITHDRAWALS: {len(withdrawals)}")
    for r in withdrawals:
        print(dict(r))

    total_cards = conn.execute("SELECT COUNT(*) AS n FROM user_cards WHERE user_id = ?", (uid,)).fetchone()["n"]
    print(f"\nTotal cards ever obtained (any rarity, any source): {total_cards}")

    pvp_wins = conn.execute("SELECT COUNT(*) AS n FROM pvp_rounds WHERE winner_id = ?", (uid,)).fetchone()["n"]
    print(f"PvP rounds won: {pvp_wins}")

    market_bought = conn.execute(
        "SELECT mo.id, mo.price_gems, mo.status, mo.created_at, c.rarity "
        "FROM market_offers mo JOIN user_cards uc ON uc.id = mo.user_card_id "
        "JOIN cards c ON c.id = uc.card_id WHERE mo.buyer_id = ? AND c.rarity = 'diamond'",
        (uid,)
    ).fetchall()
    print(f"\nMarket purchases of diamond cards: {len(market_bought)}")
    for r in market_bought:
        print(dict(r))

    swap_bought = conn.execute(
        "SELECT so.id, so.status, so.created_at, c.rarity "
        "FROM swap_offers so JOIN user_cards uc ON uc.id = so.user_card_id "
        "JOIN cards c ON c.id = uc.card_id WHERE so.buyer_id = ? AND c.rarity = 'diamond'",
        (uid,)
    ).fetchall()
    print(f"\nSwap trades receiving diamond cards: {len(swap_bought)}")
    for r in swap_bought:
        print(dict(r))

    craft_diamond = conn.execute(
        "SELECT COUNT(*) AS n FROM action_counters WHERE action = 'craft'"
    ).fetchone()
    print(f"\n(global craft counter, for reference): {dict(craft_diamond)}")
