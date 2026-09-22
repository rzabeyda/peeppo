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
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# 1 Telegram Star buys 1 gem. Change this in one place if the exchange rate ever needs to move.
GEMS_PER_STAR = 1

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
        # Days the bot has been running — shown as the "День: N" counter on Farm.
        "bot_day": db.get_bot_uptime_days(),
    }


@app.get("/api/stats")
def stats():
    """Public, no auth needed — just the running total of drops across everyone."""
    return {"total_farmed": db.get_total_farmed()}


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
        db.set_referral_notice(referral_reward["referrer_id"], who_name)

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
    credited by bot.py's successful_payment handler (gems are never granted from here)."""
    user = _authenticate(body.initData)
    if body.gems <= 0:
        raise HTTPException(400, "gems must be positive")

    import bot as bot_module

    stars = max(1, body.gems // GEMS_PER_STAR)
    link = await bot_module.create_gems_invoice(user["telegram_id"], body.gems, stars)
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
    if body.price_gems < db.MIN_LISTING_PRICE_GEMS:
        raise HTTPException(400, f"minimum price is {db.MIN_LISTING_PRICE_GEMS} gems")
    ok = db.list_card(body.user_card_id, user["telegram_id"], body.price_gems)
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
    if body.price_gems < db.MIN_LISTING_PRICE_GEMS:
        raise HTTPException(400, f"minimum price is {db.MIN_LISTING_PRICE_GEMS} gems")
    result = db.make_offer(body.user_card_id, user["telegram_id"], body.price_gems)
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
        result["listing_name"], photo_path, result["offered_names"],
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


@app.post("/api/market/history")
def market_history(body: InitDataBody):
    _authenticate(body.initData)
    return {"trades": db.get_market_history()}


@app.post("/api/swap/history")
def swap_history(body: InitDataBody):
    _authenticate(body.initData)
    return {"trades": db.get_swap_history()}


@app.post("/api/pvp/history")
def pvp_history(body: InitDataBody):
    _authenticate(body.initData)
    return {"rounds": db.get_pvp_history()}


@app.post("/api/wheel/spin")
def wheel_spin(body: InitDataBody):
    user = _authenticate(body.initData)
    result = db.spin_fortune_wheel(user["telegram_id"])
    if not result["ok"]:
        raise HTTPException(409, "already spun today")
    return {"amount": result["amount"]}
