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
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

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

    row = db.get_or_create_user(
        telegram_id=tg_user["id"],
        username=tg_user.get("username"),
        first_name=tg_user.get("first_name"),
        photo_url=tg_user.get("photo_url"),
        ref_by=ref_by,
    )
    return dict(row)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class InitDataBody(BaseModel):
    initData: str


class ShareBody(InitDataBody):
    user_card_id: int


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/api/auth")
def auth(body: InitDataBody):
    user = _authenticate(body.initData)
    return {
        "telegram_id": user["telegram_id"],
        "username": user["username"],
        "first_name": user["first_name"],
        "photo_url": user["photo_url"],
        "referrals": db.get_referral_count(user["telegram_id"]),
    }


@app.post("/api/farm")
def farm(body: InitDataBody):
    user = _authenticate(body.initData)
    result = db.farm(user["telegram_id"])
    if result is None:
        raise HTTPException(503, "card catalog is empty — add images first")
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
