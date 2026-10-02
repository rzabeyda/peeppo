"""
Peeppo — FastAPI backend for the webapp.

Serves:
  /api/auth     POST  { initData }              -> validates Telegram WebApp initData, upserts user, returns profile
  /api/farm     POST  { initData }               -> draws a random card, grants it, returns it
  /api/profile  POST  { initData }               -> returns the caller's inventory
  /api/share    POST  { initData, user_card_id }  -> sends photo + referral link to the user's own Telegram chat
  /static/cards/<filename>                        -> card images (also served directly by nginx in prod, kept here for local/dev)

All state is server-side (SQLite via database.py) — Telegram WebApp has no localStorage.
"""

import hashlib
import hmac
import json
import os
from urllib.parse import parse_qsl

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database as db

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
BOT_USERNAME = os.environ.get("BOT_USERNAME", "Peeppobot")
ADMIN_ID = os.environ.get("ADMIN_ID")  # same account bot.py uses — kept off public leaderboards
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# 1 Telegram Star buys GEMS_PER_STAR gems (100 ⭐ = 1000 гемов). Change this in one
# place if the exchange rate ever needs to move.
GEMS_PER_STAR = 10

app = FastAPI(title="Peeppo API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Telegram WebApp is served from our own domain; kept permissive for local testing
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def on_startup():
    db.init_db()


# ---------------------------------------------------------------------------
# Telegram WebApp initData validation
# https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
# ---------------------------------------------------------------------------

def validate_init_data(init_data: str) -> dict:
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        raise HTTPException(400, "malformed initData")

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise HTTPException(401, "missing hash")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise HTTPException(401, "invalid initData signature")

    user = json.loads(pairs.get("user", "{}"))
    if "id" not in user:
        raise HTTPException(401, "initData missing user")
    return user


def _authenticate(init_data: str) -> dict:
    """Validate initData and upsert the user, tracking a referral on first sight only."""
    tg_user = validate_init_data(init_data)
    pairs = dict(parse_qsl(init_data, strict_parsing=True))
    start_param = pairs.get("start_param", "")
    ref_by = None
    if start_param.startswith("ref"):
        try:
            ref_by = int(start_param[3:])
        except ValueError:
            ref_by = None

    row, is_new = db.get_or_create_user(
        telegram_id=tg_user["id"],
        username=tg_user.get("username"),
        first_name=tg_user.get("first_name"),
        photo_url=tg_user.get("photo_url"),
        ref_by=ref_by,
    )
    result = dict(row)
    result["_is_new"] = is_new
    db.settle_staking(result["telegram_id"])
    return result


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class InitDataBody(BaseModel):
    initData: str


class ShareBody(InitDataBody):
    user_card_id: int


class TransferBody(InitDataBody):
    user_card_id: int


class TransferToUsernameBody(InitDataBody):
    user_card_id: int
    username: str


class GemsInvoiceBody(InitDataBody):
    gems: int


class RankInvoiceBody(InitDataBody):
    rank: str


class ListBody(InitDataBody):
    user_card_id: int
    price_gems: int


class UnlistBody(InitDataBody):
    user_card_id: int


class BuyBody(InitDataBody):
    user_card_id: int


class OfferBody(InitDataBody):
    user_card_id: int
    price_gems: int


class SwapListBody(InitDataBody):
    user_card_id: int


class StakeBody(InitDataBody):
    user_card_id: int


class CraftBody(InitDataBody):
    user_card_id: int


class CaseOpenBody(InitDataBody):
    case_key: str


class BurnBody(InitDataBody):
    rarity: str
    user_card_ids: list[int]


class SwapOfferBody(InitDataBody):
    user_card_id: int
    offered_user_card_ids: list[int]


class PvpJoinBody(InitDataBody):
    user_card_ids: list[int]


class RedBlackPlayBody(InitDataBody):
    bet: int
    choice: str


class PlinkoPlayBody(InitDataBody):
    bet: int
    risk: str


class AviatorStartBody(InitDataBody):
    bet: int


class AviatorRoundBody(InitDataBody):
    round_id: int


class MinesStartBody(InitDataBody):
    bet: int
    mine_count: int


class MinesRoundBody(InitDataBody):
    round_id: int


class MinesRevealBody(InitDataBody):
    round_id: int
    tile: int


class CollectionDetailBody(InitDataBody):
    collection_id: int


class CollectionPlaceBody(InitDataBody):
    collection_id: int
    card_id: int


class CollectionCompletersBody(InitDataBody):
    collection_id: int


class PokerDealBody(InitDataBody):
    bet: int


class PokerDrawBody(InitDataBody):
    round_id: int
    hold_mask: list[bool]


class PokerGambleBody(InitDataBody):
    round_id: int
    choice: str


class PokerRoundBody(InitDataBody):
    round_id: int


class GameShareBody(InitDataBody):
    game: str  # "pvp" | "redblack" | "aviator" | "poker" | "mines"
    round_id: int


class CryptoWithdrawBody(InitDataBody):
    user_card_ids: list[int]
    wallet_address: str = ""


class NumberBidBody(InitDataBody):
    number: int
    amount: int


class NumberAttachBody(InitDataBody):
    number: int
    user_card_id: int


class NumberExtractBody(InitDataBody):
    user_card_id: int


class NumberListBody(InitDataBody):
    number: int
    price_gems: int


class NumberCancelListingBody(InitDataBody):
    number: int


class NumberBuyListedBody(InitDataBody):
    number: int
    user_card_id: int


class NameCreateBody(InitDataBody):
    name: str


class NameBidBody(InitDataBody):
    name: str
    amount: int


class NameListBody(InitDataBody):
    name: str
    price_gems: int


class NameCancelListingBody(InitDataBody):
    name: str


class NameBuyListedBody(InitDataBody):
    name: str


class CustomNftCreateBody(InitDataBody):
    user_card_id: int
    name: str
    number: int


class WallPinBody(InitDataBody):
    user_card_id: int


class WallUnpinBody(InitDataBody):
    user_card_id: int


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/api/auth")
def auth(body: InitDataBody):
    user = _authenticate(body.initData)
    daily_bonus = db.claim_daily_bonus(user["telegram_id"])
    bonus_info = db.get_daily_bonus_info(user["telegram_id"])
    return {
        "telegram_id": user["telegram_id"],
        "username": user["username"],
        "first_name": user["first_name"],
        "photo_url": user["photo_url"],
        "referrals": db.get_referral_count(user["telegram_id"]),
        "gems": db.get_gems(user["telegram_id"]),
        # Only true for ADMIN_ID (the dev's own account) — the ONLY account the
        # webapp is allowed to show an infinity gems display for. Every other
        # player always sees their real, honest balance.
        "is_admin": bool(ADMIN_ID) and str(user["telegram_id"]) == str(ADMIN_ID),
        "is_new": user["_is_new"],
        "daily_bonus": daily_bonus,
        "daily_bonus_amount": bonus_info["amount"],
        "daily_bonus_days_until_next": bonus_info["days_until_next"],
        # In-app "you earned 25 gems for a referral" popup — read-once, replaces the
        # old bot-DM notification (see db.set_referral_notice / /api/farm below).
        "referral_reward_notice": db.get_and_clear_referral_notice(user["telegram_id"]),
        # Whether today's (UTC) fortune-wheel spin is still unused — the frontend
        # shows the wheel overlay and calls /api/wheel/spin itself when this is true.
        "wheel_available": db.wheel_available(user["telegram_id"]),
        # Gem-mining timer status (Farm tab) — so the client can render the
        # button/countdown correctly on load without an extra round trip.
        "gem_mining": db.get_gem_mining_status(user["telegram_id"]),
        # Days the bot has been running — shown as the "День: N" counter on Farm.
        "bot_day": db.get_bot_uptime_days(),
        # Player rank (time-played tier, not card rarity) — shown next to the name in
        # Profile with matching avatar/card border colors.
        "player_rank": db.get_player_rank(user["telegram_id"]),
        # Login streak — consecutive days claim_daily_bonus() above has fired without
        # a gap. Shown as the "День: N" tile in Profile (separate from bot_day, which
        # counts days the BOT has existed, not this player's own login streak).
        "streak": db.get_streak_info(user["telegram_id"]),
    }


@app.get("/api/stats")
def stats():
    """Public, no auth needed — just the running total of drops across everyone."""
    return {"total_farmed": db.get_total_farmed()}


@app.get("/api/stats/breakdown")
def stats_breakdown():
    """Public, no auth needed — how many cards of each rarity exist across ALL players
    combined. Shown when tapping the "Карты" figure on the Farm screen (as opposed to the
    Profile screen's own personal breakdown, which is computed client-side from inventory)."""
    return db.get_global_rarity_breakdown()


@app.get("/api/cards")
def all_cards():
    """Public, no auth needed — the full catalog, for the farm animation's cycling
    preview and the 'Модели' gallery."""
    return {"cards": db.get_all_cards()}


@app.get("/api/leaderboard")
def leaderboard():
    """Public, no auth needed — the 'Топы' screen, ranked by total cards owned."""
    return {"players": db.get_leaderboard()}


@app.post("/api/farm")
async def farm(body: InitDataBody):
    user = _authenticate(body.initData)
    try:
        result = db.farm(user["telegram_id"])
    except db.InsufficientGems:
        raise HTTPException(400, "not enough gems")
    if result is None:
        raise HTTPException(503, "card catalog is empty — add images first")
    # drop_number is now global (position among every card ever farmed/crafted by
    # anyone), not per-user — see database.py's get_inventory()/farm() docstrings.
    result["farm_number"] = result["drop_number"]
    result["total_farmed"] = db.get_total_farmed()
    result["gems"] = db.get_gems(user["telegram_id"])

    referral_reward = result.pop("referral_reward", None)
    if referral_reward:
        who_name = f"@{user['username']}" if user.get("username") else (user.get("first_name") or "Реферал")
        db.set_referral_notice(referral_reward["referrer_id"], who_name, referral_reward["amount"])

    if result.get("rarity") == "diamond" and (user.get("username") or "").lower() not in ("rzabeyda", "zzabeyda"):
        import bot as bot_module
        display_name = f"@{user['username']}" if user.get("username") else (user.get("first_name") or "Игрок")
        await bot_module.notify_diamond_farmed(display_name, result["name"])

    return result


@app.post("/api/profile")
def profile(body: InitDataBody):
    user = _authenticate(body.initData)
    return {"inventory": db.get_inventory(user["telegram_id"])}


@app.post("/api/share")
async def share(body: ShareBody):
    user = _authenticate(body.initData)
    uc = db.get_user_card(body.user_card_id)
    if uc is None or uc["user_id"] != user["telegram_id"]:
        raise HTTPException(404, "card not found in your inventory")

    import bot as bot_module  # local import: avoids starting polling, reuses the same Bot instance

    photo_path = os.path.join(STATIC_DIR, "cards", uc["filename"])
    await bot_module.send_share_message(user["telegram_id"], photo_path, uc["name"])
    return {"ok": True}


@app.post("/api/transfer/start")
def transfer_start(body: TransferBody):
    """
    Owner marks one owned copy as giveable and gets back a one-time claim link.
    The frontend hands that link to Telegram's native share sheet so the owner
    picks a specific friend to send it to — no username lookup needed.
    """
    user = _authenticate(body.initData)
    ok = db.start_transfer(body.user_card_id, user["telegram_id"])
    if not ok:
        raise HTTPException(404, "card not found in your inventory")
    return {"claim_link": f"https://t.me/{BOT_USERNAME}?start=claim{body.user_card_id}"}


@app.post("/api/transfer/to_username")
async def transfer_to_username(body: TransferToUsernameBody):
    """Direct gift by @username — the recipient must have opened the bot at least once
    so we already have their telegram_id on file (Bot API can't resolve a bare username)."""
    user = _authenticate(body.initData)
    target = db.find_user_by_username(body.username)
    if target is None:
        raise HTTPException(404, "этот пользователь ещё не запускал бота")
    if target["telegram_id"] == user["telegram_id"]:
        raise HTTPException(400, "нельзя подарить самому себе")

    try:
        result = db.transfer_card_to(body.user_card_id, user["telegram_id"], target["telegram_id"])
    except db.InsufficientGems:
        raise HTTPException(400, "not enough gems")
    if result is None:
        raise HTTPException(404, "card not found in your inventory, or it's busy (listed/staked/in a round)")

    import bot as bot_module

    from_name = f"@{user['username']}" if user.get("username") else (user.get("first_name") or "Игрок")
    photo_path = os.path.join(STATIC_DIR, "cards", result["filename"])
    await bot_module.notify_gift_received(target["telegram_id"], from_name, result["name"], photo_path)
    return {"ok": True, "to_name": target["username"] or target["first_name"]}


# ---------------------------------------------------------------------------
# Gems (bought with Telegram Stars)
# ---------------------------------------------------------------------------

@app.post("/api/gems/invoice")
async def gems_invoice(body: GemsInvoiceBody):
    """Returns a Telegram Stars invoice link for the requested amount of gems.
    The frontend opens it in-app with tg.openInvoice(); a successful payment is
    credited by bot.py's successful_payment handler (gems are never granted from here).
    Gems must be a multiple of GEMS_PER_STAR (10) since Stars can't be fractional —
    100 ⭐ buys exactly 1000 gems, not some rounded-off amount."""
    user = _authenticate(body.initData)
    if body.gems <= 0 or body.gems % GEMS_PER_STAR != 0:
        raise HTTPException(400, f"gems must be a positive multiple of {GEMS_PER_STAR}")

    import bot as bot_module

    stars = body.gems // GEMS_PER_STAR
    link = await bot_module.create_gems_invoice(user["telegram_id"], body.gems, stars)
    return {"invoice_link": link, "stars": stars}


@app.post("/api/rank/invoice")
async def rank_invoice(body: RankInvoiceBody):
    """Returns a Telegram Stars invoice link to buy a player rank (RANK_STARS_PRICE).
    Same pattern as /api/gems/invoice: the frontend opens the link with tg.openInvoice(),
    and bot.py's successful_payment handler applies the rank once Telegram confirms
    payment — nothing is granted from here."""
    user = _authenticate(body.initData)
    if body.rank not in db.RANK_STARS_PRICE:
        raise HTTPException(400, "this rank isn't for sale")
    current_tier = db.get_player_rank_tier(user["telegram_id"])
    if db.RANK_TIERS.index(body.rank) <= current_tier:
        raise HTTPException(400, "you already have this rank or higher")

    import bot as bot_module

    stars = db.RANK_STARS_PRICE[body.rank]
    link = await bot_module.create_rank_invoice(user["telegram_id"], body.rank, stars)
    return {"invoice_link": link, "stars": stars}


# ---------------------------------------------------------------------------
# Market
# ---------------------------------------------------------------------------

@app.post("/api/market/listings")
def market_listings(body: InitDataBody):
    """Listings are anonymous — the seller's identity never leaves the server
    beyond the is_mine flag needed to show "Твой лот" on the buyer's own listing."""
    user = _authenticate(body.initData)
    listings = db.get_market_listings()
    for item in listings:
        item["is_mine"] = item["seller_id"] == user["telegram_id"]
        del item["seller_id"], item["username"], item["first_name"]
    return {"listings": listings, "gems": db.get_gems(user["telegram_id"])}


@app.post("/api/market/list")
def market_list(body: ListBody):
    user = _authenticate(body.initData)
    try:
        ok = db.list_card(body.user_card_id, user["telegram_id"], body.price_gems)
    except db.ListingPriceTooLow as e:
        raise HTTPException(400, f"minimum price is {e.min_price} gems")
    if not ok:
        raise HTTPException(404, "card not found in your inventory")
    return {"ok": True}


@app.post("/api/market/unlist")
def market_unlist(body: UnlistBody):
    user = _authenticate(body.initData)
    ok = db.unlist_card(body.user_card_id, user["telegram_id"])
    if not ok:
        raise HTTPException(404, "listing not found")
    return {"ok": True}


@app.post("/api/market/buy")
async def market_buy(body: BuyBody):
    user = _authenticate(body.initData)
    result = db.buy_listing(body.user_card_id, user["telegram_id"])
    if result is None:
        raise HTTPException(400, "listing unavailable or not enough gems")

    import bot as bot_module

    buyer_name = f"@{user['username']}" if user.get("username") else (user.get("first_name") or "Игрок")
    await bot_module.notify_card_sold(result["seller_id"], buyer_name, result["name"], result["price"])
    return {"ok": True}


@app.post("/api/market/offer")
async def market_offer(body: OfferBody):
    user = _authenticate(body.initData)
    try:
        result = db.make_offer(body.user_card_id, user["telegram_id"], body.price_gems)
    except db.ListingPriceTooLow as e:
        raise HTTPException(400, f"minimum price is {e.min_price} gems")
    if result is None:
        raise HTTPException(400, "listing unavailable")

    import bot as bot_module

    buyer_name = f"@{user['username']}" if user.get("username") else (user.get("first_name") or "Игрок")
    photo_path = os.path.join(STATIC_DIR, "cards", result["filename"])
    await bot_module.notify_new_offer(
        result["seller_id"], result["offer_id"], buyer_name, result["name"], photo_path, body.price_gems
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Swap / barter market
# ---------------------------------------------------------------------------

@app.post("/api/swap/listings")
def swap_listings(body: InitDataBody):
    """Anonymous, same as /api/market/listings — only is_mine leaves the server."""
    user = _authenticate(body.initData)
    listings = db.get_swap_listings()
    for item in listings:
        item["is_mine"] = item["seller_id"] == user["telegram_id"]
        del item["seller_id"]
    return {"listings": listings}


@app.post("/api/swap/list")
def swap_list(body: SwapListBody):
    user = _authenticate(body.initData)
    ok = db.list_for_swap(body.user_card_id, user["telegram_id"])
    if not ok:
        raise HTTPException(400, "card not found in your inventory, or already listed for sale/swap")
    return {"ok": True}


@app.post("/api/swap/unlist")
def swap_unlist(body: SwapListBody):
    user = _authenticate(body.initData)
    ok = db.unlist_swap(body.user_card_id, user["telegram_id"])
    if not ok:
        raise HTTPException(404, "listing not found")
    return {"ok": True}


@app.post("/api/craft")
def craft(body: CraftBody):
    user = _authenticate(body.initData)
    try:
        result = db.craft_card(user["telegram_id"], body.user_card_id)
    except db.CraftNotOwned:
        raise HTTPException(404, "card not found in your inventory, or it's busy (staked/listed for sale or swap/in a PvP round)")
    except db.InsufficientGems:
        raise HTTPException(400, "not enough gems")
    result["gems"] = db.get_gems(user["telegram_id"])
    return result


@app.post("/api/case/open")
def case_open(body: CaseOpenBody):
    user = _authenticate(body.initData)
    try:
        result = db.open_case(user["telegram_id"], body.case_key)
    except db.CaseNotFound:
        raise HTTPException(404, "unknown case")
    except db.InsufficientGems:
        raise HTTPException(400, "not enough gems")
    result["gems"] = db.get_gems(user["telegram_id"])
    return result


@app.post("/api/burn")
def burn(body: BurnBody):
    user = _authenticate(body.initData)
    try:
        result = db.burn_cards(user["telegram_id"], body.rarity, body.user_card_ids)
    except db.BurnNotAllowed:
        raise HTTPException(400, "this rarity can't be burned (already top tier, or unknown)")
    except db.BurnNotEnoughCards:
        raise HTTPException(400, "not enough eligible cards of this rarity (need more, or some are busy)")
    return result


@app.post("/api/stake/list")
def stake_list(body: StakeBody):
    user = _authenticate(body.initData)
    try:
        ok = db.stake_card(body.user_card_id, user["telegram_id"])
    except db.InsufficientGems:
        raise HTTPException(400, "not enough gems")
    except db.StakeLimitReached:
        raise HTTPException(400, f"stake limit reached (max {db.MAX_STAKED_CARDS} cards at once)")
    if not ok:
        raise HTTPException(400, "card not found in your inventory, or already staked/listed for sale or swap")
    return {"ok": True, "gems": db.get_gems(user["telegram_id"])}


@app.post("/api/stake/unstake")
def stake_unstake(body: StakeBody):
    user = _authenticate(body.initData)
    ok = db.unstake_card(body.user_card_id, user["telegram_id"])
    if not ok:
        raise HTTPException(404, "card isn't staked")
    return {"ok": True, "gems": db.get_gems(user["telegram_id"])}


@app.post("/api/swap/offer")
async def swap_offer(body: SwapOfferBody):
    user = _authenticate(body.initData)
    if not body.offered_user_card_ids:
        raise HTTPException(400, "offer at least one card")
    result = db.propose_swap(body.user_card_id, user["telegram_id"], body.offered_user_card_ids)
    if result is None:
        raise HTTPException(400, "listing unavailable, or you don't own one of the offered cards")

    import bot as bot_module

    buyer_name = f"@{user['username']}" if user.get("username") else (user.get("first_name") or "Игрок")
    photo_path = os.path.join(STATIC_DIR, "cards", result["listing_filename"])
    await bot_module.notify_new_swap_offer(
        result["seller_id"], result["offer_id"], buyer_name,
        result["listing_name"], photo_path, result["offered_cards"],
    )
    return {"ok": True}


@app.post("/api/pvp/state")
async def pvp_state(body: InitDataBody):
    user = _authenticate(body.initData)
    # Round results are no longer DM'd — the in-app spin-the-wheel reveal (unseen_result
    # in get_pvp_state) is the only place a result is shown now. Still have to resolve
    # any round whose countdown elapsed, just without notifying anyone by DM for it.
    db.resolve_due_pvp_rounds()
    return db.get_pvp_state(user["telegram_id"])


@app.post("/api/pvp/join")
async def pvp_join(body: PvpJoinBody):
    user = _authenticate(body.initData)
    db.resolve_due_pvp_rounds()
    if not body.user_card_ids:
        raise HTTPException(400, "stake at least one card")
    try:
        state = db.join_pvp_round(user["telegram_id"], body.user_card_ids)
    except db.PvpCardNotOwned:
        raise HTTPException(400, "one of the cards isn't yours, or is already busy (listed/staked/in a round)")
    except db.PvpRoundLocked:
        raise HTTPException(409, "round already locked, try again in a moment")

    # No chat ping when a new round opens anymore — the in-app pulsing PvP dot
    # (see webapp's checkPvpAmbient) is the only "something's happening" signal now.
    return state


@app.post("/api/pvp/invite")
async def pvp_invite(body: InitDataBody):
    """"Позвать игрока" button -- pings PUBLIC_CHAT to invite others into the open PvP
    bank. Rate-limited per player server-side (db.try_pvp_invite)."""
    user = _authenticate(body.initData)
    if not db.is_in_open_pvp_round(user["telegram_id"]):
        raise HTTPException(400, "not_joined: stake cards in the pvp bank first")
    if db.get_open_pvp_participant_count() >= 2:
        raise HTTPException(400, "already_full: bank already has 2+ players")
    claim = db.try_pvp_invite(user["telegram_id"])
    if not claim["ok"]:
        raise HTTPException(429, f"cooldown: {claim['seconds_left']}s left")

    import bot as bot_module

    name = f"@{user['username']}" if user.get("username") else (user.get("first_name") or "Игрок")
    ok = await bot_module.send_pvp_invite(name)
    if not ok:
        raise HTTPException(502, "could not post to chat")
    return {"ok": True}


@app.post("/api/market/history")
def market_history(body: InitDataBody):
    _authenticate(body.initData)
    return {"trades": db.get_market_history()}


@app.post("/api/swap/history")
def swap_history(body: InitDataBody):
    _authenticate(body.initData)
    return {"trades": db.get_swap_history()}


# ---------------------------------------------------------------------------
# Blind swap -- no specific listing to pick, just a rarity. Put up one card,
# get back whatever the next matching player put up for the same rarity.
# ---------------------------------------------------------------------------

@app.post("/api/blindswap/status")
def blindswap_status(body: InitDataBody):
    user = _authenticate(body.initData)
    return {"listings": db.get_blind_swap_status(user["telegram_id"])}


@app.post("/api/blindswap/list")
async def blindswap_list(body: SwapListBody):
    user = _authenticate(body.initData)
    result = db.list_for_blind_swap(body.user_card_id, user["telegram_id"])
    if result is None:
        raise HTTPException(400, "card not found in your inventory, busy, or you already have a pending blind listing for this rarity")
    if result["matched"]:
        import bot as bot_module
        my_photo = os.path.join(STATIC_DIR, "cards", result["received_filename"])
        partner_photo = os.path.join(STATIC_DIR, "cards", result["given_filename"])
        await bot_module.notify_blind_swap_match(user["telegram_id"], result["given_name"], result["received_name"], my_photo)
        await bot_module.notify_blind_swap_match(result["partner_id"], result["received_name"], result["given_name"], partner_photo)
    return result


@app.post("/api/blindswap/unlist")
def blindswap_unlist(body: SwapListBody):
    user = _authenticate(body.initData)
    ok = db.unlist_blind_swap(body.user_card_id, user["telegram_id"])
    if not ok:
        raise HTTPException(404, "listing not found, or already matched")
    return {"ok": True}


@app.post("/api/pvp/history")
def pvp_history(body: InitDataBody):
    _authenticate(body.initData)
    return {"rounds": db.get_pvp_history()}


@app.post("/api/pvp/leaderboard")
def pvp_leaderboard(body: InitDataBody):
    """Top-10 by total PvP round wins, for the "Топ 10" tab next to Правила/История.
    ADMIN_ID (if set) is left out, same as the /ref referral leaderboard."""
    _authenticate(body.initData)
    return {"leaderboard": db.get_pvp_win_leaderboard(12, exclude_id=int(ADMIN_ID) if ADMIN_ID else None)}


@app.post("/api/pvp/leaderboard/cards")
def pvp_leaderboard_cards(body: InitDataBody):
    """Top-10 by total cards captured from opponents across all won PvP rounds."""
    _authenticate(body.initData)
    return {"leaderboard": db.get_pvp_cards_won_leaderboard(12, exclude_id=int(ADMIN_ID) if ADMIN_ID else None)}


@app.post("/api/pvp/leaderboard/diamond")
def pvp_leaderboard_diamond(body: InitDataBody):
    """Top-10 by total DIAMOND-rarity cards captured from opponents across all won PvP rounds."""
    _authenticate(body.initData)
    return {"leaderboard": db.get_pvp_diamond_cards_won_leaderboard(12, exclude_id=int(ADMIN_ID) if ADMIN_ID else None)}


@app.post("/api/redblack/play")
def redblack_play(body: RedBlackPlayBody):
    """In-app Red&Black -- fully independent from the chat /redblack game (no shared
    state, no chat_id at all: play_redblack() is already chat-agnostic)."""
    user = _authenticate(body.initData)
    try:
        return db.play_redblack(user["telegram_id"], body.bet, body.choice)
    except db.RedBlackError as e:
        raise HTTPException(400, str(e))
    except db.InsufficientGems:
        raise HTTPException(400, "not enough gems")


@app.post("/api/plinko/play")
def plinko_play(body: PlinkoPlayBody):
    """One Plinko drop -- see play_plinko() for the full mechanic."""
    user = _authenticate(body.initData)
    try:
        return db.play_plinko(user["telegram_id"], body.bet, body.risk)
    except db.PlinkoError as e:
        raise HTTPException(400, str(e))
    except db.InsufficientGems:
        raise HTTPException(400, "not enough gems")


@app.post("/api/aviator/start")
def aviator_start(body: AviatorStartBody):
    """In-app Aviator -- fully independent from the chat /go game (chat_id IS NULL on
    the round it creates, see count_active_aviator_rounds_for_user()). One flying
    round at a time per player, same as the chat version's one-active-round-per-chat
    rule, just scoped to the player instead of a chat."""
    user = _authenticate(body.initData)
    if db.count_active_aviator_rounds_for_user(user["telegram_id"]) > 0:
        raise HTTPException(409, "already have an active round -- cash out or wait for it to crash first")
    try:
        result = db.start_aviator(user["telegram_id"], body.bet)
    except db.AviatorError as e:
        raise HTTPException(400, str(e))
    except db.InsufficientGems:
        raise HTTPException(400, "not enough gems")
    return {"round_id": result["round_id"]}  # crash_point is server-side only, never sent to the client


@app.post("/api/aviator/state")
def aviator_state(body: AviatorRoundBody):
    """Polled by the client every tick interval while a round is flying -- computes
    the current multiplier from elapsed wall-clock time server-side (see
    _aviator_tick_state()), so there's no background ticker to keep alive for an
    in-app round."""
    user = _authenticate(body.initData)
    try:
        return db.get_aviator_state(body.round_id, user["telegram_id"])
    except db.AviatorError as e:
        raise HTTPException(404, str(e))


@app.post("/api/aviator/cashout")
def aviator_cashout(body: AviatorRoundBody):
    """Cashes out at whatever multiplier the server independently computes from
    elapsed time RIGHT NOW -- the client can never claim its own multiplier."""
    user = _authenticate(body.initData)
    try:
        return db.cashout_aviator_now(body.round_id, user["telegram_id"])
    except db.AviatorError as e:
        raise HTTPException(400, str(e))


@app.post("/api/mines/start")
def mines_start(body: MinesStartBody):
    """One open board at a time per player, same rule as Aviator's one-flying-round
    cap."""
    user = _authenticate(body.initData)
    if db.count_active_mines_rounds_for_user(user["telegram_id"]) > 0:
        raise HTTPException(409, "already have an active board -- cash out or hit a mine first")
    try:
        return db.start_mines(user["telegram_id"], body.bet, body.mine_count)
    except db.MinesError as e:
        raise HTTPException(400, str(e))
    except db.InsufficientGems:
        raise HTTPException(400, "not enough gems")


@app.post("/api/mines/my_active")
def mines_my_active(body: InitDataBody):
    """Lets the client recover an orphaned active board after a reload/crash/backgrounded
    app -- called once every time the player opens the Mines tab, see
    get_active_mines_round_for_user(). Returns null if they have no active round."""
    user = _authenticate(body.initData)
    return db.get_active_mines_round_for_user(user["telegram_id"])


@app.post("/api/mines/state")
def mines_state(body: MinesRoundBody):
    """For a client reload mid-round -- returns exactly what the board should be
    showing right now."""
    user = _authenticate(body.initData)
    try:
        return db.get_mines_state(body.round_id, user["telegram_id"])
    except db.MinesError as e:
        raise HTTPException(404, str(e))


@app.post("/api/mines/reveal")
def mines_reveal(body: MinesRevealBody):
    """Opens one tile -- a mine ends the round, a safe tile raises the multiplier
    (and auto-cashes-out if that was the last safe tile on the board)."""
    user = _authenticate(body.initData)
    try:
        return db.reveal_mines_tile(body.round_id, user["telegram_id"], body.tile)
    except db.MinesError as e:
        raise HTTPException(400, str(e))


@app.post("/api/mines/cashout")
def mines_cashout(body: MinesRoundBody):
    """Cashes out at whatever multiplier the player's current reveals are worth."""
    user = _authenticate(body.initData)
    try:
        return db.cashout_mines(body.round_id, user["telegram_id"])
    except db.MinesError as e:
        raise HTTPException(400, str(e))


@app.post("/api/mines/history")
def mines_history(body: InitDataBody):
    _authenticate(body.initData)
    return {"rounds": db.get_mines_history()}


@app.post("/api/mines/leaderboard")
def mines_leaderboard(body: InitDataBody):
    _authenticate(body.initData)
    return {"leaderboard": db.get_mines_leaderboard(12)}


@app.post("/api/withdrawals/top")
def withdrawals_top(body: InitDataBody):
    _authenticate(body.initData)
    return {"leaderboard": db.get_recent_gem_withdrawals()}


@app.post("/api/poker/deal")
def poker_deal(body: PokerDealBody):
    """Charges the bet and deals a fresh 5-card hand -- everything server-side, the
    client only ever supplies the bet amount."""
    user = _authenticate(body.initData)
    try:
        return db.deal_poker(user["telegram_id"], body.bet)
    except db.PokerError as e:
        raise HTTPException(400, str(e))
    except db.InsufficientGems:
        raise HTTPException(400, "not enough gems")


@app.post("/api/poker/draw")
def poker_draw(body: PokerDrawBody):
    """Replaces the non-held cards, evaluates the hand, and credits any win --
    hold_mask must be exactly 5 booleans, same order as the cards dealt."""
    user = _authenticate(body.initData)
    try:
        return db.draw_poker(body.round_id, user["telegram_id"], body.hold_mask)
    except db.PokerError as e:
        raise HTTPException(400, str(e))


@app.post("/api/poker/gamble")
def poker_gamble(body: PokerGambleBody):
    """Double-up: a fair 50/50 flip on the current running payout."""
    user = _authenticate(body.initData)
    try:
        return db.gamble_poker(body.round_id, user["telegram_id"], body.choice)
    except db.PokerError as e:
        raise HTTPException(400, str(e))


@app.post("/api/poker/collect")
def poker_collect(body: PokerRoundBody):
    """Closes out a won round -- gems were already credited live, this is just a
    status flip."""
    user = _authenticate(body.initData)
    try:
        return db.collect_poker(body.round_id, user["telegram_id"])
    except db.PokerError as e:
        raise HTTPException(400, str(e))


@app.post("/api/poker/history")
def poker_history(body: InitDataBody):
    _authenticate(body.initData)
    return {"rounds": db.get_poker_history()}


@app.post("/api/poker/leaderboard")
def poker_leaderboard(body: InitDataBody):
    _authenticate(body.initData)
    return {"leaderboard": db.get_poker_leaderboard(12)}


@app.post("/api/redblack/history")
def redblack_history(body: InitDataBody):
    _authenticate(body.initData)
    return {"rounds": db.get_redblack_history()}


@app.post("/api/redblack/leaderboard")
def redblack_leaderboard(body: InitDataBody):
    _authenticate(body.initData)
    return {"leaderboard": db.get_redblack_leaderboard(12)}


@app.post("/api/plinko/history")
def plinko_history(body: InitDataBody):
    _authenticate(body.initData)
    return {"rounds": db.get_plinko_history()}


@app.post("/api/plinko/leaderboard")
def plinko_leaderboard(body: InitDataBody):
    _authenticate(body.initData)
    return {"leaderboard": db.get_plinko_leaderboard(12)}


@app.post("/api/aviator/history")
def aviator_history(body: InitDataBody):
    _authenticate(body.initData)
    return {"rounds": db.get_aviator_history()}


@app.post("/api/aviator/leaderboard")
def aviator_leaderboard(body: InitDataBody):
    _authenticate(body.initData)
    return {"leaderboard": db.get_aviator_leaderboard(12)}


@app.post("/api/games/share")
async def games_share(body: GameShareBody):
    """Shared by the "Поделиться" button on every Игры sub-tab (PvP / Ракетка /
    Red&Black) -- verifies the round actually belongs to (or involved) this player,
    then bot.py re-derives the outcome server-side from the DB and posts one message
    into PUBLIC_CHAT. The client never supplies the outcome text/numbers itself."""
    user = _authenticate(body.initData)
    import bot as bot_module

    if body.game == "redblack":
        round_row = db.get_redblack_round(body.round_id)
        if round_row is None or round_row["user_id"] != user["telegram_id"]:
            raise HTTPException(404, "round not found")
        ok = await bot_module.share_redblack_result(body.round_id)
    elif body.game == "aviator":
        round_row = db.get_aviator_round(body.round_id)
        if round_row is None or round_row["user_id"] != user["telegram_id"]:
            raise HTTPException(404, "round not found")
        ok = await bot_module.share_aviator_result(body.round_id)
    elif body.game == "pvp":
        round_row = db.get_last_resolved_pvp_round(body.round_id)
        if round_row is None or not any(p["user_id"] == user["telegram_id"] for p in round_row["participants"]):
            raise HTTPException(404, "round not found")
        ok = await bot_module.share_pvp_result(body.round_id)
    elif body.game == "poker":
        round_row = db.get_poker_round(body.round_id)
        if round_row is None or round_row["user_id"] != user["telegram_id"]:
            raise HTTPException(404, "round not found")
        ok = await bot_module.share_poker_result(body.round_id)
    elif body.game == "mines":
        round_row = db.get_mines_round(body.round_id)
        if round_row is None or round_row["user_id"] != user["telegram_id"]:
            raise HTTPException(404, "round not found")
        ok = await bot_module.share_mines_result(body.round_id)
    else:
        raise HTTPException(400, "unknown game")

    if not ok:
        raise HTTPException(502, "could not post to chat")
    return {"ok": True}


@app.post("/api/wheel/spin")
def wheel_spin(body: InitDataBody):
    user = _authenticate(body.initData)
    result = db.spin_fortune_wheel(user["telegram_id"])
    if not result["ok"]:
        raise HTTPException(409, "already spun today")
    return {"amount": result["amount"]}


@app.post("/api/gemmining/collect")
def gem_mining_collect(body: InitDataBody):
    user = _authenticate(body.initData)
    result = db.collect_gem_mining(user["telegram_id"])
    result["gems"] = db.get_gems(user["telegram_id"])
    return result


# ---------------------------------------------------------------------------
# Crypto withdrawal — "Продать Diamond карты за GRAM" (repurposes the old
# "Продать Гемы за Звёзды" button in the gems-choice overlay). See database.py's
# request_crypto_withdrawal()/get_pending_withdrawal() for the full mechanics.
# ---------------------------------------------------------------------------

@app.post("/api/crypto/status")
def crypto_status(body: InitDataBody):
    """The caller's own currently-pending request, if any, so the frontend can show
    "заявка на рассмотрении" instead of the picker."""
    user = _authenticate(body.initData)
    return {"pending": db.get_pending_withdrawal(user["telegram_id"])}


# ---------------------------------------------------------------------------
# Collections ("Альбомы") -- themed sub-sets of the card catalog the player
# fills in one slot at a time. Placing a card never locks/consumes it (same copy
# stays fully usable for market/PvP/staking) -- pure completion tracking, with an
# achievement recorded the first time every slot in a collection is filled. See
# database.py's get_collections_overview()/get_collection_detail()/place_collection_card().
# ---------------------------------------------------------------------------

async def _announce_collection_completions(user: dict, collection_names: list[str]):
    """Posts to PUBLIC_CHAT for every newly-completed collection in this request --
    skipped entirely for rzabeyda/zzabeyda (dev/test accounts), same exclusion the
    diamond-farm announcement already uses."""
    if not collection_names:
        return
    if (user.get("username") or "").lower() in ("rzabeyda", "zzabeyda"):
        return
    import bot as bot_module
    display_name = f"@{user['username']}" if user.get("username") else (user.get("first_name") or "Игрок")
    for name in collection_names:
        await bot_module.notify_collection_completed(display_name, name)


@app.post("/api/collections")
async def collections_overview(body: InitDataBody):
    user = _authenticate(body.initData)
    collections = db.get_collections_overview(user["telegram_id"])
    just_completed_names = [c["name"] for c in collections if c.get("just_completed")]
    await _announce_collection_completions(user, just_completed_names)
    response = {"collections": collections}
    # A just-completed collection just paid out COLLECTION_COMPLETE_REWARD_GEMS gems
    # (see _maybe_complete_collection()) -- re-read the live balance so the client can
    # show the new total right away instead of waiting for the next unrelated gems-returning
    # call to refresh it.
    if just_completed_names:
        response["gems"] = db.get_gems(user["telegram_id"])
    return response


@app.post("/api/collections/detail")
async def collections_detail(body: CollectionDetailBody):
    user = _authenticate(body.initData)
    try:
        detail = db.get_collection_detail(user["telegram_id"], body.collection_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    if detail.get("just_completed"):
        await _announce_collection_completions(user, [detail["name"]])
        detail["gems"] = db.get_gems(user["telegram_id"])
    return detail


@app.post("/api/collections/place")
async def collections_place(body: CollectionPlaceBody):
    user = _authenticate(body.initData)
    try:
        result = db.place_collection_card(user["telegram_id"], body.collection_id, body.card_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if result.get("newly_completed") and result.get("collection_name"):
        await _announce_collection_completions(user, [result["collection_name"]])
        result["gems"] = db.get_gems(user["telegram_id"])
    return result


@app.post("/api/collections/completers")
def collections_completers(body: CollectionCompletersBody):
    _authenticate(body.initData)
    return {"completers": db.get_collection_completers(body.collection_id)}


@app.post("/api/crypto/withdraw")
async def crypto_withdraw(body: CryptoWithdrawBody):
    user = _authenticate(body.initData)
    if not body.user_card_ids:
        raise HTTPException(400, "select at least 10 Diamond cards")
    try:
        result = db.request_crypto_withdrawal(user["telegram_id"], body.user_card_ids, body.wallet_address)
    except db.CryptoWithdrawalError as e:
        raise HTTPException(400, e.message)

    import bot as bot_module

    display_name = f"@{user['username']}" if user.get("username") else (user.get("first_name") or "Игрок")
    await bot_module.notify_admin_withdrawal_request(
        result["withdrawal_id"], user["telegram_id"], display_name,
        result["card_count"], result["gram_amount"], result["wallet_address"],
    )
    return {"ok": True, "withdrawal_id": result["withdrawal_id"], "gram_amount": result["gram_amount"]}


# ---------------------------------------------------------------------------
# Card-number auctions — see database.py's card_numbers schema comment for the state
# machine. "Номера" tab: browse the cheapest/lowest free-or-auctioned numbers, bid on
# one, and once you win it, attach it to a card of yours or resell it to someone else.
# ---------------------------------------------------------------------------

@app.post("/api/numbers/board")
def numbers_board(body: InitDataBody):
    user = _authenticate(body.initData)
    board = db.get_numbers_board()
    board["gems"] = db.get_gems(user["telegram_id"])
    return board


@app.post("/api/numbers/mine")
def numbers_mine(body: InitDataBody):
    user = _authenticate(body.initData)
    return {"numbers": db.get_my_numbers(user["telegram_id"])}


@app.post("/api/numbers/owners")
def numbers_owners(body: InitDataBody):
    """"Владельцы" tab -- every tracked number currently owned, and by whom."""
    _authenticate(body.initData)
    return {"numbers": db.get_all_owned_numbers()}


@app.post("/api/numbers/bid")
def numbers_bid(body: NumberBidBody):
    user = _authenticate(body.initData)
    try:
        result = db.place_number_bid(user["telegram_id"], body.number, body.amount)
    except db.NumberNotAvailable:
        raise HTTPException(404, "number isn't up for auction right now")
    except db.NumberBidTooLow as e:
        raise HTTPException(400, f"minimum bid is {e.min_bid} gems")
    except db.InsufficientGems:
        raise HTTPException(400, "not enough gems")
    return result


@app.post("/api/numbers/attach")
def numbers_attach(body: NumberAttachBody):
    user = _authenticate(body.initData)
    try:
        result = db.attach_number(user["telegram_id"], body.number, body.user_card_id)
    except db.NumberNotAvailable:
        raise HTTPException(404, "you don't own this number")
    except db.NumberCardNotUsable:
        raise HTTPException(404, "card not found in your inventory, or it's busy (listed/staked/swapped/in a PvP round)")
    return result


@app.post("/api/numbers/extract")
def numbers_extract(body: NumberExtractBody):
    user = _authenticate(body.initData)
    try:
        result = db.extract_card_number(user["telegram_id"], body.user_card_id)
    except db.NumberNotRare:
        raise HTTPException(400, "this card's number isn't rare enough to extract")
    except db.NumberCardNotUsable:
        raise HTTPException(404, "card not found in your inventory, or it's busy (listed/staked/swapped/in a PvP round/giveaway)")
    except db.InsufficientGems:
        raise HTTPException(400, "not enough gems")
    return result


@app.post("/api/numbers/list")
def numbers_list(body: NumberListBody):
    user = _authenticate(body.initData)
    try:
        db.list_number_for_sale(user["telegram_id"], body.number, body.price_gems)
    except db.ListingPriceTooLow as e:
        raise HTTPException(400, f"minimum price is {e.min_price} gems")
    except db.NumberNotAvailable:
        raise HTTPException(400, "number not available -- it's either not yours or still pinned to a card (unpin it first)")
    return {"ok": True}


@app.post("/api/numbers/cancel_listing")
def numbers_cancel_listing(body: NumberCancelListingBody):
    user = _authenticate(body.initData)
    try:
        db.cancel_number_listing(user["telegram_id"], body.number)
    except db.NumberCardNotUsable:
        raise HTTPException(404, "listing not found")
    return {"ok": True}


@app.post("/api/numbers/buy_listed")
def numbers_buy_listed(body: NumberBuyListedBody):
    user = _authenticate(body.initData)
    try:
        result = db.buy_listed_number(user["telegram_id"], body.number, body.user_card_id)
    except db.NumberNotAvailable:
        raise HTTPException(404, "listing no longer available")
    except db.NumberCardNotUsable:
        raise HTTPException(404, "card not found in your inventory, or it's busy (listed/staked/swapped/in a PvP round)")
    except db.InsufficientGems:
        raise HTTPException(400, "not enough gems")
    result["gems"] = db.get_gems(user["telegram_id"])
    return result


# ---------------------------------------------------------------------------
# Custom-name marketplace ("Имена") + Custom NFT ("Obsidian").
# ---------------------------------------------------------------------------

@app.post("/api/names/board")
def names_board(body: InitDataBody):
    user = _authenticate(body.initData)
    board = db.get_names_board()
    board["gems"] = db.get_gems(user["telegram_id"])
    return board


@app.post("/api/names/mine")
def names_mine(body: InitDataBody):
    user = _authenticate(body.initData)
    return {"names": db.get_my_names(user["telegram_id"])}


@app.post("/api/names/owners")
def names_owners(body: InitDataBody):
    """"Владельцы" tab -- every owned name and by whom."""
    _authenticate(body.initData)
    return {"names": db.get_all_owned_names()}


@app.post("/api/names/create")
def names_create(body: NameCreateBody):
    user = _authenticate(body.initData)
    try:
        result = db.create_name_auction(user["telegram_id"], body.name)
    except db.NameInvalid:
        raise HTTPException(400, f"name must be {db.CUSTOM_NAME_MIN_LEN}-{db.CUSTOM_NAME_MAX_LEN} English letters/digits")
    except db.NameTaken:
        raise HTTPException(400, "this name is already taken")
    except db.InsufficientGems:
        raise HTTPException(400, "not enough gems")
    return result


@app.post("/api/names/bid")
def names_bid(body: NameBidBody):
    user = _authenticate(body.initData)
    try:
        result = db.place_name_bid(user["telegram_id"], body.name, body.amount)
    except db.NameNotAvailable:
        raise HTTPException(404, "name isn't up for auction right now")
    except db.NameBidTooLow as e:
        raise HTTPException(400, f"minimum bid is {e.min_bid} gems")
    except db.InsufficientGems:
        raise HTTPException(400, "not enough gems")
    return result


@app.post("/api/names/list")
def names_list(body: NameListBody):
    user = _authenticate(body.initData)
    try:
        db.list_name_for_sale(user["telegram_id"], body.name, body.price_gems)
    except db.ListingPriceTooLow as e:
        raise HTTPException(400, f"minimum price is {e.min_price} gems")
    except db.NameNotAvailable:
        raise HTTPException(404, "you don't own this name")
    return {"ok": True}


@app.post("/api/names/cancel_listing")
def names_cancel_listing(body: NameCancelListingBody):
    user = _authenticate(body.initData)
    try:
        db.cancel_name_listing(user["telegram_id"], body.name)
    except db.NameNotAvailable:
        raise HTTPException(404, "listing not found")
    return {"ok": True}


@app.post("/api/names/buy_listed")
def names_buy_listed(body: NameBuyListedBody):
    user = _authenticate(body.initData)
    try:
        result = db.buy_listed_name(user["telegram_id"], body.name)
    except db.NameNotAvailable:
        raise HTTPException(404, "listing no longer available")
    except db.InsufficientGems:
        raise HTTPException(400, "not enough gems")
    result["gems"] = db.get_gems(user["telegram_id"])
    return result


@app.post("/api/custom_nft/create")
def custom_nft_create(body: CustomNftCreateBody):
    user = _authenticate(body.initData)
    try:
        result = db.create_custom_nft(user["telegram_id"], body.user_card_id, body.name, body.number)
    except db.InsufficientGems:
        raise HTTPException(
            400,
            f"not enough gems ({db.CUSTOM_NFT_CREATE_COST_GEMS} to create Obsidian, "
            f"{db.CUSTOM_NFT_EDIT_COST_GEMS} to change the name/number on one you already made)",
        )
    except db.NumberCardNotUsable:
        raise HTTPException(404, "card not found in your inventory, or it's busy (listed/staked/swapped/in a PvP round)")
    except db.NameNotAvailable:
        raise HTTPException(404, "you don't own this name")
    except db.NumberNotAvailable:
        raise HTTPException(404, "you don't own this number")
    return result


@app.post("/api/obsidian/list")
def obsidian_list(body: InitDataBody):
    """Full catalog of every Obsidian card across all players -- backs the "OBSIDIAN"
    browse button in Модели."""
    _authenticate(body.initData)
    return {"cards": db.get_all_obsidian_cards()}


@app.post("/api/wall/list")
def wall_list(body: InitDataBody):
    user = _authenticate(body.initData)
    return {"cards": db.get_wall(user["telegram_id"]), "max_cards": db.MAX_WALL_CARDS}


@app.post("/api/wall/pin")
def wall_pin(body: WallPinBody):
    user = _authenticate(body.initData)
    try:
        db.pin_to_wall(user["telegram_id"], body.user_card_id)
    except db.WallCardNotUsable:
        raise HTTPException(404, "card not found in your inventory")
    except db.WallFull:
        raise HTTPException(400, f"wall is full (max {db.MAX_WALL_CARDS} cards)")
    return {"cards": db.get_wall(user["telegram_id"])}


@app.post("/api/wall/unpin")
def wall_unpin(body: WallUnpinBody):
    user = _authenticate(body.initData)
    db.unpin_from_wall(user["telegram_id"], body.user_card_id)
    return {"cards": db.get_wall(user["telegram_id"])}
