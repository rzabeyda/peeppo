"""
Peeppo — aiogram bot.

Responsibilities:
  - /start with no payload -> register user, show Open button
  - /start ref<telegram_id> -> referral deep link, credits whoever invited this new user
  - /start claim<user_card_id> -> a friend opens a "Передать" gift link from the webapp;
    if that copy is still pending transfer, ownership moves to whoever clicks it
  - send_share_message() -> called by api.py when a user taps "Поделиться" in the webapp;
    sends the card photo + the user's own referral link back into their own chat so they can
    just hit Telegram's native Forward/Share-to-story on it.

Run as its own long-polling process (peeppo_bot.service), separate from api.py (peeppo_api.service),
same pattern as the other bots on this server.
"""

import asyncio
import logging
import os
import random
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineQuery,
    InlineQueryResultPhoto,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

import database as db

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://peeppo.memstroy.app")
_split = urlsplit(WEBAPP_URL)
WEBAPP_ORIGIN = f"{_split.scheme}://{_split.netloc}"  # WEBAPP_URL minus any ?query — safe to append /static/... to
BOT_USERNAME = os.environ.get("BOT_USERNAME", "Peeppobot")  # no leading @
ADMIN_ID = os.environ.get("ADMIN_ID")  # your own telegram_id — set in .env to get "new user" pings
PUBLIC_CHAT = os.environ.get("PUBLIC_CHAT_USERNAME", "@peeppo_chat")  # public chat: PvP stakes + admin /gem drops
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "@peeppo_channel")  # channel: admin /giveaway posts
# One-time referral-race leaderboard announcement, posted to PUBLIC_CHAT. 15:00 Moscow
# time on Sep 30 2026 — see ref_race_scheduler() below.
REF_RACE_ANNOUNCE_AT = datetime(2026, 9, 30, 15, 0, tzinfo=ZoneInfo("Europe/Moscow"))
STATIC_CARDS_DIR = Path(__file__).parent / "static" / "cards"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("peeppo.bot")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def _open_button():
    kb = InlineKeyboardBuilder()
    kb.button(text="Фармить", web_app=WebAppInfo(url=WEBAPP_URL))
    kb.button(text="Чат", url="https://t.me/peeppo_chat")
    kb.button(text="Канал", url="https://t.me/peeppo_channel")
    kb.adjust(1, 2)  # "Фармить" on its own row, "Чат"/"Канал" side by side below it
    return kb.as_markup()


@dp.message(CommandStart())
async def handle_start(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""

    ref_by = None
    if payload.startswith("ref"):
        try:
            ref_by = int(payload[3:])
        except ValueError:
            ref_by = None

    user_row, is_new = db.get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        ref_by=ref_by,
    )
    if is_new:
        referrer_row = db.get_user(user_row["ref_by"]) if user_row["ref_by"] else None
        await notify_admin_new_user(message.from_user, referrer_row)
        # No more "joined via your link" / "earned 25 gems" bot DMs — the referrer
        # now sees the reward as an in-app popup on their own next visit instead
        # (see db.set_referral_notice / /api/auth's referral_reward_notice).
        await check_hundred_club()

    if payload.startswith("giveaway"):
        try:
            giveaway_id = int(payload[len("giveaway"):])
        except ValueError:
            giveaway_id = None
        if giveaway_id is not None:
            status = db.join_giveaway(giveaway_id, message.from_user.id)
            if status == "joined":
                await message.answer("✅ Ты в розыгрыше! Итоги подведём в канале — следи за постом.")
            elif status == "already_joined":
                await message.answer("Ты уже участвуешь в этом розыгрыше 👍")
            elif status == "drawn":
                await message.answer("Этот розыгрыш уже завершён.")
        # falls through to the normal welcome below, same as a referral link —
        # the whole point is onboarding them into the game too, not just the entry.

    if payload.startswith("claim"):
        await _handle_claim(message, payload)
        return

    await message.answer_photo(
        photo=FSInputFile(STATIC_CARDS_DIR / "black_pepe.jpg"),
        caption=(
            "Добро Пожаловать в <b>Peeppo</b>!\n\n"
            "Жми Фарм — собирай карточки, показывай друзьям, меняйся и продавай на рынке."
        ),
        reply_markup=_open_button(),
        parse_mode="HTML",
    )


async def notify_admin_new_user(tg_user, referrer_row=None):
    """Best-effort ping to you (ADMIN_ID in .env) whenever someone brand new starts the
    bot — names who invited them too, when they came via a referral link."""
    if not ADMIN_ID:
        return
    who = f"@{tg_user.username}" if tg_user.username else (tg_user.first_name or str(tg_user.id))
    if referrer_row is not None:
        ref_who = (
            f"@{referrer_row['username']}" if referrer_row["username"]
            else (referrer_row["first_name"] or str(referrer_row["telegram_id"]))
        )
        text = f"{ref_who} привёл {who}"
    else:
        text = f"Новый юзер {who}"
    try:
        await bot.send_message(int(ADMIN_ID), text)
    except Exception:
        logger.warning("could not notify admin of new user %s", tg_user.id)


# ---------------------------------------------------------------------------
# Admin commands — only usable by ADMIN_ID (set in .env). Everyone else is
# silently ignored, so these never even show up as "unknown command" for players.
# ---------------------------------------------------------------------------

def _is_admin(user_id: int) -> bool:
    return bool(ADMIN_ID) and str(user_id) == str(ADMIN_ID)


def _resolve_user(ref: str):
    """Admin commands accept either a numeric telegram_id or a @username."""
    ref = ref.strip()
    bare = ref.lstrip("@")
    if bare.isdigit():
        return db.get_user(int(bare))
    return db.find_user_by_username(ref)


@dp.message(Command("admin"))
async def handle_admin_panel(message: Message):
    if not _is_admin(message.from_user.id):
        return
    stats = db.get_admin_stats()
    await message.answer(
        "👑 <b>Админ-панель Peeppo</b>\n\n"
        f"Юзеры: <b>{stats['users']}</b>\n"
        f"Сегодня: <b>{stats['active_today']}</b>\n"
        f"Карты: <b>{stats['total_farmed']}</b>\n"
        f"Кейсы: <b>{stats['cases_bought']}</b>\n"
        f"Крафт: <b>{stats['cards_crafted']}</b>\n"
        f"Эволюция: <b>{stats['cards_evolved']}</b>\n\n"
        "<b>Команды:</b>\n"
        "/addgem id_или_@username кол-во — начислить гемы\n"
        "/cardgiveaway [редкость] [кол-во] [мин] [макс] — мгновенный розыгрыш ТВОИХ карт среди всех юзеров бота\n"
        "/numbergiveaway номер [часов] — розыгрыш ТВОЕЙ карты с этим номером живьём в чате (кнопка «Участвовать», по умолчанию 1 час)",
        parse_mode="HTML",
    )


@dp.message(Command("addgem", "addgems"))
async def handle_admin_add_gems(message: Message):
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("Использование: /addgem telegram_id_или_@username количество")
        return
    try:
        amount = int(parts[2])
    except ValueError:
        await message.answer("количество должно быть числом")
        return
    user = _resolve_user(parts[1])
    if user is None:
        await message.answer("Такого юзера нет в базе")
        return
    new_balance = db.add_gems(user["telegram_id"], amount)
    who = f"@{user['username']}" if user["username"] else str(user["telegram_id"])
    await message.answer(f"Готово — у {who} теперь {new_balance} 💎")


@dp.message(Command("givecard"))
async def handle_admin_give_card(message: Message):
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("Использование: /givecard telegram_id_или_@username card_id")
        return
    try:
        card_id = int(parts[2])
    except ValueError:
        await message.answer("card_id должен быть числом")
        return
    user = _resolve_user(parts[1])
    if user is None:
        await message.answer("Такого юзера нет в базе")
        return
    card = db.get_card_by_id(card_id)
    if card is None:
        await message.answer("Нет такой карточки в каталоге (проверь ID)")
        return
    db.grant_card(user["telegram_id"], card_id)
    who = f"@{user['username']}" if user["username"] else str(user["telegram_id"])
    await message.answer(f"Выдал «{card['name'] or card['filename']}» юзеру {who}")


@dp.message(Command("finduser"))
async def handle_admin_find_user(message: Message):
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Использование: /finduser username")
        return
    user = db.find_user_by_username(parts[1])
    if user is None:
        await message.answer("Не найден")
        return
    await message.answer(
        f"id: <code>{user['telegram_id']}</code>\n"
        f"@{user['username'] or '—'} ({user['first_name'] or '—'})\n"
        f"гемов: {user['gems']}",
        parse_mode="HTML",
    )


GEM_DROP_AMOUNT = 100

# Auto-scheduler: fires roughly once every hour, only between 06:00 and 00:00
# (midnight) Tallinn local time. GEM_DROP_MIN_GAP_SECONDS guards against firing a
# second drop too soon if the bot process restarts a few times in a row (e.g. during
# a deploy) — kept a bit below GEM_DROP_INTERVAL_SECONDS so the +/-180s jitter on the
# sleep below never causes a legitimate hourly drop to be skipped.
GEM_DROP_TZ = ZoneInfo("Europe/Tallinn")
GEM_DROP_START_HOUR = 6
GEM_DROP_END_HOUR = 24
GEM_DROP_INTERVAL_SECONDS = 3600
GEM_DROP_MIN_GAP_SECONDS = 3000


async def _post_gem_drop(amount: int = GEM_DROP_AMOUNT, label: str = "💎 Дроп") -> bool:
    """Creates a gem drop and posts the "Забрать" button into PUBLIC_CHAT. Shared by
    the manual /gem command and the automatic hourly scheduler."""
    drop_id = db.create_gem_drop(amount)
    kb = InlineKeyboardBuilder()
    kb.button(text="Забрать", callback_data=f"gem_claim:{drop_id}")
    try:
        await bot.send_message(
            PUBLIC_CHAT,
            f"{label} {amount} гемов! Кто первый нажмёт «Забрать» — тому и достанется.",
            reply_markup=kb.as_markup(),
        )
        return True
    except Exception:
        logger.warning("could not post gem drop to %s", PUBLIC_CHAT)
        return False


@dp.message(Command("gem"))
async def handle_admin_gem_drop(message: Message):
    """Admin-only: posts a first-come-first-served gem drop with a "Забрать" button
    into PUBLIC_CHAT, no matter which chat (including a private DM) the admin typed
    /gem from."""
    if not _is_admin(message.from_user.id):
        return
    ok = await _post_gem_drop()
    if not ok:
        await message.answer(f"Не удалось отправить дроп в {PUBLIC_CHAT} — бот точно там состоит?")


async def gem_drop_scheduler():
    """Background loop living for the lifetime of the bot process: roughly once every
    hour, checks whether it's currently 06:00-00:00 in Tallinn and — if no drop went
    out too recently — posts an automatic GEM_DROP_AMOUNT-gem drop into PUBLIC_CHAT."""
    logger.info("gem drop scheduler started (06:00-00:00 Europe/Tallinn, ~every 1h, %d gems)", GEM_DROP_AMOUNT)
    while True:
        try:
            now_local = datetime.now(GEM_DROP_TZ)
            if GEM_DROP_START_HOUR <= now_local.hour < GEM_DROP_END_HOUR:
                last = db.get_last_gem_drop_time()
                due = True
                if last:
                    last_dt = datetime.fromisoformat(last)
                    due = (datetime.now(timezone.utc) - last_dt).total_seconds() >= GEM_DROP_MIN_GAP_SECONDS
                if due:
                    ok = await _post_gem_drop()
                    if ok:
                        logger.info("auto gem drop posted at %s Tallinn time", now_local.strftime("%H:%M"))
        except Exception:
            logger.exception("gem drop scheduler iteration failed")
        await asyncio.sleep(GEM_DROP_INTERVAL_SECONDS + random.randint(-180, 180))


@dp.callback_query(F.data.startswith("gem_claim:"))
async def handle_gem_claim(call: CallbackQuery):
    drop_id = int(call.data.split(":")[1])
    # Register the claimer even if they've never DM'd the bot before — anyone in the
    # group chat can tap "Забрать", not just people who already opened the webapp.
    db.get_or_create_user(
        telegram_id=call.from_user.id,
        username=call.from_user.username,
        first_name=call.from_user.first_name,
        ref_by=None,
    )
    result = db.claim_gem_drop(drop_id, call.from_user.id)
    if result["ok"]:
        who = f"@{call.from_user.username}" if call.from_user.username else (call.from_user.first_name or "игрок")
        try:
            await call.message.edit_text(f"✅ Дроп {result['amount']} 💎 забрал(а) {who}")
        except Exception:
            logger.warning("could not edit gem drop message %s after claim", drop_id)
        await call.answer(f"Тебе начислено {result['amount']} 💎!", show_alert=True)
    elif result["claimed_by_name"]:
        await call.answer(f"Уже забрал(а) {result['claimed_by_name']}", show_alert=True)
    else:
        await call.answer("Дроп больше не активен", show_alert=True)


# ---------------------------------------------------------------------------
# Channel giveaways: /giveaway [гемов] [победителей] [часов] (admin-only, all args
# optional — defaults 200/10/24) posts a "Розыгрыш" into CHANNEL_USERNAME with a
# deep-link "Участвовать" button (?start=giveaway<id>). Anyone who taps it and opens
# the bot is entered — new player or existing, doesn't matter. After the given number
# of hours, giveaway_scheduler auto-draws winners_count random entrants and edits
# that same channel post with the results.
# ---------------------------------------------------------------------------

def _format_hours(hours: float) -> str:
    if float(hours).is_integer():
        return f"{int(hours)} ч."
    return f"{hours:g} ч."


@dp.message(Command("giveaway"))
async def handle_admin_giveaway(message: Message):
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    amount, winners_count, hours = 200, 10, 24.0
    try:
        if len(parts) > 1:
            amount = int(parts[1])
        if len(parts) > 2:
            winners_count = int(parts[2])
        if len(parts) > 3:
            hours = float(parts[3])
    except ValueError:
        await message.answer("Формат: /giveaway [гемов] [победителей] [часов], например /giveaway 200 10 24")
        return

    giveaway_id = db.create_giveaway(amount, winners_count, hours)
    link = f"https://t.me/{BOT_USERNAME}?start=giveaway{giveaway_id}"
    kb = InlineKeyboardBuilder()
    kb.button(text="Участвовать", url=link)
    text = (
        f"🎉 Розыгрыш!\n\n"
        f"{winners_count} игроков получат по {amount} 💎 каждый.\n"
        f"Жми «Участвовать» — итоги подведём тут же через {_format_hours(hours)}."
    )
    try:
        sent = await bot.send_message(CHANNEL_USERNAME, text, reply_markup=kb.as_markup())
    except Exception:
        await message.answer(f"Не удалось опубликовать в {CHANNEL_USERNAME} — бот точно там админ?")
        return
    db.set_giveaway_message(giveaway_id, sent.message_id)
    await message.answer(f"Розыгрыш #{giveaway_id} опубликован в {CHANNEL_USERNAME}. Итоги через {_format_hours(hours)}.")


@dp.message(Command("cardgiveaway"))
async def handle_admin_card_giveaway(message: Message):
    """Admin-only: /cardgiveaway [редкость] [кол-во карт] [мин] [макс] — instantly gives
    away up to that many of the ADMIN'S OWN cards of that rarity (defaults:
    bronze/100/1/5), min..max at a time, randomly among every other registered user
    (no chat-activity tracking involved — anyone who has ever started the bot is
    eligible). Resolves immediately and posts the results into PUBLIC_CHAT — unlike
    /giveaway there's no waiting window."""
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    rarity, total_cards, min_c, max_c = "bronze", 100, 1, 5
    try:
        if len(parts) > 1:
            rarity = parts[1].lower()
        if len(parts) > 2:
            total_cards = int(parts[2])
        if len(parts) > 3:
            min_c = int(parts[3])
        if len(parts) > 4:
            max_c = int(parts[4])
    except ValueError:
        await message.answer("Формат: /cardgiveaway [редкость] [кол-во] [мин] [макс], например /cardgiveaway bronze 100 1 5")
        return

    try:
        result = db.create_card_giveaway(message.from_user.id, rarity, total_cards, min_c, max_c)
    except db.CardGiveawayError as e:
        await message.answer(f"Не удалось разыграть: {e}")
        return

    lines = "\n".join(
        f"{('@' + w['username']) if w['username'] else (w['first_name'] or str(w['telegram_id']))} — {w['count']} шт."
        for w in result["winners"]
    )
    text = (
        f"🎉 Розыгрыш {rarity}-карт завершён!\n\n"
        f"Разыграно {result['total_distributed']} карт(ы) среди {len(result['winners'])} игроков:\n\n"
        f"{lines}"
    )
    try:
        await bot.send_message(PUBLIC_CHAT, text)
    except Exception:
        logger.warning("could not announce card giveaway to %s", PUBLIC_CHAT)
    await message.answer(f"Готово — {result['total_distributed']} карт разыграно среди {len(result['winners'])} игроков, результат опубликован в {PUBLIC_CHAT}.")


@dp.message(Command("numbergiveaway"))
async def handle_admin_number_giveaway(message: Message):
    """Admin-only: /numbergiveaway номер [часов] — posts a "Розыгрыш" of the ONE of
    the admin's own cards currently showing that display number into PUBLIC_CHAT with
    an "Участвовать" button, exactly like the regular in-chat gem drops (no deep link
    needed — PUBLIC_CHAT is a real chat the bot is already a member of, so a plain
    callback button works). After duration_hours (default 1), giveaway_scheduler picks
    one random entrant and transfers the card to them."""
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Формат: /numbergiveaway номер [часов], например /numbergiveaway 67 1")
        return
    try:
        number = int(parts[1])
        hours = float(parts[2]) if len(parts) > 2 else 1.0
    except ValueError:
        await message.answer("Формат: /numbergiveaway номер [часов], например /numbergiveaway 67 1")
        return

    try:
        result = db.create_number_giveaway(message.from_user.id, number, hours)
    except db.NumberGiveawayError as e:
        await message.answer(f"Не удалось разыграть: {e}")
        return

    card = result["card"]
    kb = InlineKeyboardBuilder()
    kb.button(text="Участвовать", callback_data=f"numgiveaway_join:{result['id']}")
    text = (
        f"🎉 Розыгрыш карты №{card['number']}!\n\n"
        f"«{card['name'] or card['rarity']}» ({card['rarity']}) достанется одному случайному участнику.\n"
        f"Жми «Участвовать» — итоги подведём тут же через {_format_hours(hours)}."
    )
    try:
        sent = await bot.send_message(PUBLIC_CHAT, text, reply_markup=kb.as_markup())
    except Exception:
        await message.answer(f"Не удалось опубликовать в {PUBLIC_CHAT} — бот точно там состоит?")
        return
    db.set_number_giveaway_message(result["id"], sent.message_id)
    await message.answer(f"Розыгрыш карты №{number} опубликован в {PUBLIC_CHAT}. Итоги через {_format_hours(hours)}.")


@dp.callback_query(F.data.startswith("numgiveaway_join:"))
async def handle_number_giveaway_join(call: CallbackQuery):
    giveaway_id = int(call.data.split(":")[1])
    # Register the tapper even if they've never DM'd the bot before — same as gem_claim.
    db.get_or_create_user(
        telegram_id=call.from_user.id,
        username=call.from_user.username,
        first_name=call.from_user.first_name,
        ref_by=None,
    )
    status = db.join_number_giveaway(giveaway_id, call.from_user.id)
    if status == "joined":
        await call.answer("Ты участвуешь! Удачи 🍀", show_alert=True)
    elif status == "already_joined":
        await call.answer("Ты уже участвуешь", show_alert=True)
    elif status == "is_admin":
        await call.answer("Нельзя участвовать в своём же розыгрыше", show_alert=True)
    elif status == "drawn":
        await call.answer("Розыгрыш уже завершён", show_alert=True)
    else:
        await call.answer("Розыгрыш не найден", show_alert=True)


async def _announce_number_giveaway_result(giveaway: dict, result: dict):
    card = result["card"]
    if result["winner"] and result["transferred"]:
        w = result["winner"]
        who = f"@{w['username']}" if w["username"] else (w["first_name"] or str(w["telegram_id"]))
        text = (
            f"🎉 Розыгрыш карты №{card['number']} завершён!\n\n"
            f"Участников: {result['total_entries']}\n"
            f"«{card['name'] or card['rarity']}» уходит игроку {who}!"
        )
    elif result["winner"] and not result["transferred"]:
        text = (
            f"🎉 Розыгрыш карты №{card['number']} завершён, но приз не выдан: {result['reason']}. "
            f"Загляни в /admin, чтобы разобраться."
        )
    else:
        text = f"🎉 Розыгрыш карты №{card['number']} завершён — участников не набралось, увы."
    try:
        if giveaway.get("message_id"):
            await bot.edit_message_text(chat_id=PUBLIC_CHAT, message_id=giveaway["message_id"], text=text)
        else:
            await bot.send_message(PUBLIC_CHAT, text)
    except Exception:
        logger.warning("could not announce number giveaway %s result", giveaway["id"])


async def _announce_giveaway_result(giveaway: dict, result: dict):
    winners = result["winners"]
    if winners:
        names = ", ".join(
            (f"@{w['username']}" if w["username"] else (w["first_name"] or str(w["telegram_id"])))
            for w in winners
        )
        text = (
            f"🎉 Розыгрыш завершён!\n\n"
            f"Участников: {result['total_entries']}\n"
            f"Победители (+{result['amount']} 💎 каждому): {names}"
        )
    else:
        text = "🎉 Розыгрыш завершён — участников не набралось, увы. Ждите следующий!"
    try:
        if giveaway.get("message_id"):
            await bot.edit_message_text(chat_id=CHANNEL_USERNAME, message_id=giveaway["message_id"], text=text)
        else:
            await bot.send_message(CHANNEL_USERNAME, text)
    except Exception:
        logger.warning("could not announce giveaway %s result", giveaway["id"])


async def giveaway_scheduler():
    """Background loop living for the lifetime of the bot process: every few minutes,
    checks for giveaways whose draw time has passed and draws them — both the regular
    gem giveaways and the admin's card-by-number chat giveaways."""
    logger.info("giveaway scheduler started")
    while True:
        try:
            for giveaway in db.get_due_giveaways():
                result = db.draw_giveaway(giveaway["id"])
                await _announce_giveaway_result(giveaway, result)
            for giveaway in db.get_due_number_giveaways():
                result = db.draw_number_giveaway(giveaway["id"])
                await _announce_number_giveaway_result(giveaway, result)
        except Exception:
            logger.exception("giveaway scheduler iteration failed")
        await asyncio.sleep(300)


async def check_hundred_club():
    """Fires (at most once, ever) the "hundred club" contest the moment there are
    HUNDRED_CLUB_SIZE registered players: draws 10 random winners from the first 100
    and announces the result in PUBLIC_CHAT. Cheap and idempotent — safe to call on
    every new registration and on every bot startup."""
    try:
        result = db.maybe_run_hundred_club_contest()
    except Exception:
        logger.exception("hundred club check failed")
        return
    if result is None:
        return
    names = ", ".join(
        (f"@{w['username']}" if w["username"] else (w["first_name"] or str(w["telegram_id"])))
        for w in result["winners"]
    )
    text = (
        f"🎊 Нас стало {result['total_entrants']}!\n\n"
        f"Конкурс среди первых {result['total_entrants']} игроков подведён — "
        f"победители (+{result['amount']} 💎 каждому): {names}"
    )
    try:
        await bot.send_message(PUBLIC_CHAT, text)
    except Exception:
        logger.warning("could not announce hundred club contest result")


async def _handle_claim(message: Message, payload: str):
    try:
        user_card_id = int(payload[len("claim"):])
    except ValueError:
        return

    result = db.claim_transfer(user_card_id, message.from_user.id)
    if result is None:
        await message.answer(
            "Эта ссылка уже использована или недействительна.",
            reply_markup=_open_button(),
        )
        return

    name = result["name"] or "картинка"
    photo_path = STATIC_CARDS_DIR / result["filename"]
    await message.answer_photo(
        photo=FSInputFile(photo_path),
        caption=f"Тебе подарили «{name}»! 🎁 Она уже у тебя в профиле.",
        reply_markup=_open_button(),
    )

    who = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
    try:
        await bot.send_message(
            result["from_user_id"],
            f"{who} забрал(а) твой подарок «{name}» 🎁",
        )
    except Exception:
        logger.warning("could not notify original owner %s", result["from_user_id"])


# ---------------------------------------------------------------------------
# Gems — bought with Telegram Stars (currency "XTR", provider_token left empty)
# ---------------------------------------------------------------------------

async def create_gems_invoice(user_id: int, gems: int, stars: int) -> str:
    """Called from api.py when the user taps the gems balance and enters an amount.
    Payload encodes the buyer + gems so the successful_payment handler below knows
    exactly what to credit once Telegram confirms the Stars payment."""
    payload = f"gems:{user_id}:{gems}"
    return await bot.create_invoice_link(
        title="Гемы Peeppo",
        description=f"{gems} 💎 гемов — трать их на рынке картинок",
        payload=payload,
        provider_token="",  # empty provider_token is required for Telegram Stars
        currency="XTR",
        prices=[LabeledPrice(label=f"{gems} гемов", amount=stars)],
    )


async def create_rank_invoice(user_id: int, rank: str, stars: int) -> str:
    """Called from api.py when the user taps a "Купить" button on a rank row.
    Payload encodes the buyer + rank name so the successful_payment handler below
    knows what to apply once Telegram confirms the Stars payment. This is a
    one-time cosmetic purchase, not gems, so payload uses "rank:" not "gems:"."""
    payload = f"rank:{user_id}:{rank}"
    return await bot.create_invoice_link(
        title=f"Ранг {rank.upper()} — Peeppo",
        description=f"Статус {rank.upper()} в профиле — навсегда, не влияет на игру",
        payload=payload,
        provider_token="",  # empty provider_token is required for Telegram Stars
        currency="XTR",
        prices=[LabeledPrice(label=f"Ранг {rank.upper()}", amount=stars)],
    )


@dp.pre_checkout_query()
async def handle_pre_checkout(pre_checkout_q: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)


@dp.message(F.successful_payment)
async def handle_successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload
    if payload.startswith("gems:"):
        _, uid_str, gems_str = payload.split(":")
        gems = int(gems_str)
        new_balance = db.add_gems(int(uid_str), gems)
        await message.answer(f"Зачислено {gems} 💎! Баланс: {new_balance} 💎")
    elif payload.startswith("rank:"):
        _, uid_str, rank = payload.split(":")
        new_rank = db.set_purchased_rank(int(uid_str), rank)
        await message.answer(f"Ранг {new_rank.upper()} куплен! 🏆")


# ---------------------------------------------------------------------------
# Market — offers ("Оценить") are accepted/declined from the seller's own DM
# ---------------------------------------------------------------------------

async def notify_card_sold(seller_id: int, buyer_name: str, card_name: str | None, price: int):
    """Best-effort — a direct "Купить" purchase needs no seller action, just an FYI."""
    name = card_name or "картинка"
    try:
        await bot.send_message(seller_id, f"{buyer_name} купил(а) твою «{name}» за {price} 💎")
    except Exception:
        logger.warning("could not notify seller %s of a sale", seller_id)


async def notify_diamond_farmed(who_name: str, card_name: str):
    """Posted to PUBLIC_CHAT whenever anyone farms a Diamond card — social-proof/FOMO
    hook so the chat sees big drops happening live. Best-effort, never blocks the farm."""
    try:
        await bot.send_message(
            PUBLIC_CHAT,
            f"🎉 Игрок {who_name} зафармил «{card_name}» (Diamond)!",
        )
    except Exception:
        logger.warning("could not announce diamond farm to %s", PUBLIC_CHAT)


async def notify_new_offer(seller_id: int, offer_id: int, buyer_name: str, card_name: str | None,
                            photo_path: str, price_gems: int):
    name = card_name or "картинка"
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Принять", callback_data=f"offer_accept:{offer_id}")
    kb.button(text="❌ Отклонить", callback_data=f"offer_decline:{offer_id}")
    kb.adjust(2)
    try:
        await bot.send_photo(
            chat_id=seller_id,
            photo=FSInputFile(photo_path),
            caption=f"{buyer_name} предлагает {price_gems} 💎 за «{name}»",
            reply_markup=kb.as_markup(),
        )
    except Exception:
        logger.warning("could not notify seller %s of a new offer", seller_id)


@dp.callback_query(F.data.startswith("offer_accept:"))
async def handle_offer_accept(call: CallbackQuery):
    offer_id = int(call.data.split(":")[1])
    result = db.accept_offer(offer_id, call.from_user.id)
    if result is None:
        await call.answer("Оффер уже неактуален", show_alert=True)
        return
    name = result["name"] or "картинка"
    await call.message.edit_caption(caption=f"Принято! «{name}» продана за {result['price']} 💎")
    await call.answer("Готово")
    try:
        await bot.send_message(result["buyer_id"], f"Твоё предложение приняли! «{name}» уже у тебя в профиле 🎉")
    except Exception:
        logger.warning("could not notify buyer %s of accepted offer", result["buyer_id"])


@dp.callback_query(F.data.startswith("offer_decline:"))
async def handle_offer_decline(call: CallbackQuery):
    offer_id = int(call.data.split(":")[1])
    result = db.decline_offer(offer_id, call.from_user.id)
    if result is None:
        await call.answer("Оффер уже неактуален", show_alert=True)
        return
    name = result["name"] or "картинка"
    await call.message.edit_caption(caption=f"Отклонено — «{name}» осталась у тебя")
    await call.answer("Отклонено")
    try:
        await bot.send_message(result["buyer_id"], f"Продавец отклонил твоё предложение по «{name}» 🙅")
    except Exception:
        logger.warning("could not notify buyer %s of declined offer", result["buyer_id"])


# ---------------------------------------------------------------------------
# Swap / barter market — offers are accepted/declined from the seller's own DM,
# same pattern as the gems market above.
# ---------------------------------------------------------------------------

# Display order for the grouped-by-rarity swap offer message — low to high, matching
# how the seller reads it: "what's on the table" from least to most valuable.
SWAP_RARITY_ORDER = ["bronze", "silver", "gold", "platinum", "diamond"]


async def notify_new_swap_offer(seller_id: int, offer_id: int, buyer_name: str, listing_name: str | None,
                                 photo_path: str, offered_cards: list[dict]):
    """offered_cards: [{"name": ..., "rarity": ...}, ...] — grouped by rarity in the DM so
    the seller can tell at a glance whether they're being offered bronze junk or a diamond,
    instead of a flat list of names with no rarity shown at all."""
    name = listing_name or "картинка"
    grouped: dict[str, list[str]] = {}
    for card in offered_cards:
        grouped.setdefault(card.get("rarity") or "bronze", []).append(card["name"])
    lines = []
    for rarity in SWAP_RARITY_ORDER:
        names = grouped.get(rarity)
        if names:
            quoted = " ".join(f"«{n}»" for n in names)
            lines.append(f"{rarity.upper()} {quoted}")
    offered_block = "\n".join(lines)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Принять", callback_data=f"swap_accept:{offer_id}")
    kb.button(text="❌ Отклонить", callback_data=f"swap_decline:{offer_id}")
    kb.adjust(2)
    try:
        await bot.send_photo(
            chat_id=seller_id,
            photo=FSInputFile(photo_path),
            caption=f"{buyer_name} предлагает обменять на твою «{name}»:\n\n{offered_block}",
            reply_markup=kb.as_markup(),
        )
    except Exception:
        logger.warning("could not notify seller %s of a new swap offer", seller_id)


@dp.callback_query(F.data.startswith("swap_accept:"))
async def handle_swap_accept(call: CallbackQuery):
    offer_id = int(call.data.split(":")[1])
    result = db.accept_swap_offer(offer_id, call.from_user.id)
    if result is None:
        await call.answer("Обмен уже неактуален", show_alert=True)
        return
    name = result["name"] or "картинка"
    await call.message.edit_caption(caption=f"Обмен принят — «{name}» ушла новому владельцу")
    await call.answer("Готово")
    try:
        await bot.send_message(result["buyer_id"], f"Твой обмен приняли! «{name}» уже у тебя в профиле 🎉")
    except Exception:
        logger.warning("could not notify buyer %s of accepted swap", result["buyer_id"])


@dp.callback_query(F.data.startswith("swap_decline:"))
async def handle_swap_decline(call: CallbackQuery):
    offer_id = int(call.data.split(":")[1])
    result = db.decline_swap_offer(offer_id, call.from_user.id)
    if result is None:
        await call.answer("Обмен уже неактуален", show_alert=True)
        return
    name = result["name"] or "картинка"
    await call.message.edit_caption(caption=f"Отклонено — «{name}» осталась у тебя")
    await call.answer("Отклонено")
    try:
        await bot.send_message(result["buyer_id"], f"Продавец отклонил твой обмен по «{name}» 🙅")
    except Exception:
        logger.warning("could not notify buyer %s of declined swap", result["buyer_id"])


async def notify_gift_received(to_user_id: int, from_name: str, card_name: str | None, photo_path: str):
    """Called from api.py's /api/transfer/to_username after a direct, no-claim-link gift."""
    name = card_name or "картинка"
    try:
        await bot.send_photo(
            chat_id=to_user_id,
            photo=FSInputFile(photo_path),
            caption=f"{from_name} подарил(а) тебе «{name}»! 🎁 Она уже у тебя в профиле.",
            reply_markup=_open_button(),
        )
    except Exception:
        logger.warning("could not notify gift recipient %s", to_user_id)


async def send_share_message(user_id: int, photo_path: str, card_name: str | None):
    """Called from api.py (imports this module directly, same BOT_TOKEN) after /api/share.
    Kept as a fallback for clients where tg.switchInlineQuery isn't available (see
    handle_inline_share below for the main "Поделиться" flow, which skips this chat entirely)."""
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref{user_id}"
    caption = (
        f"Смотри что мне выпало «{card_name}» 🎁 Залетай в Peeppo и фарми карты → {ref_link}"
        if card_name else
        f"Смотри что мне выпало 🎁 Залетай в Peeppo и фарми карты → {ref_link}"
    )
    await bot.send_photo(chat_id=user_id, photo=FSInputFile(photo_path), caption=caption)


# ---------------------------------------------------------------------------
# Crypto withdrawal — "Продать Diamond карты за GRAM" (repurposes the old
# "Продать Гемы за Звёзды" button in the gems-choice overlay). See database.py's
# request_crypto_withdrawal()/admin_pay_withdrawal()/admin_cancel_withdrawal() for
# the mechanics — the selected cards are held (voided) the instant a request is
# submitted, so they can't be double-spent while it's pending. Only ADMIN_ID can
# act on the request; the admin sends the GRAM manually outside this system and
# then taps "Оплатить", or taps "Отменить" to give the cards back.
# ---------------------------------------------------------------------------

def _withdrawal_text(withdrawal_id: int, card_count: int, gram_amount: int, wallet_address: str,
                      status_line: str = "") -> str:
    text = (
        f"💰 <b>Заявка на вывод GRAM</b> #{withdrawal_id}\n\n"
        f"Карт Diamond: <b>{card_count}</b>\n"
        f"К выплате: <b>{gram_amount} GRAM</b>\n"
        f"Кошелёк: <code>{wallet_address}</code>"
    )
    if status_line:
        text += f"\n\n{status_line}"
    return text


async def notify_admin_withdrawal_request(withdrawal_id: int, user_id: int, display_name: str,
                                           card_count: int, gram_amount: int, wallet_address: str):
    if not ADMIN_ID:
        logger.warning("ADMIN_ID not set — cannot notify admin of withdrawal request %s", withdrawal_id)
        return
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Оплатить", callback_data=f"crypto_pay:{withdrawal_id}")
    kb.button(text="❌ Отменить", callback_data=f"crypto_cancel:{withdrawal_id}")
    kb.adjust(2)
    text = (
        f"Игрок: {display_name} (id {user_id})\n\n" +
        _withdrawal_text(withdrawal_id, card_count, gram_amount, wallet_address) +
        "\n\nОтправь GRAM вручную на этот адрес, затем нажми «Оплатить»."
    )
    try:
        await bot.send_message(int(ADMIN_ID), text, parse_mode="HTML", reply_markup=kb.as_markup())
    except Exception:
        logger.warning("could not notify admin of withdrawal request %s", withdrawal_id)


@dp.callback_query(F.data.startswith("crypto_pay:"))
async def handle_crypto_pay(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return
    withdrawal_id = int(call.data.split(":")[1])
    result = db.admin_pay_withdrawal(withdrawal_id)
    if result is None:
        await call.answer("Заявка уже обработана", show_alert=True)
        return
    try:
        await call.message.edit_text(
            _withdrawal_text(withdrawal_id, result["card_count"], result["gram_amount"],
                              result["wallet_address"], "✅ <b>Оплачено</b>"),
            parse_mode="HTML",
        )
    except Exception:
        logger.warning("could not edit withdrawal message %s after pay", withdrawal_id)
    await call.answer("Отмечено как оплачено")
    try:
        await bot.send_message(
            result["user_id"],
            f"💰 Твоя заявка на вывод {result['gram_amount']} GRAM оплачена! Спасибо, что играешь в Peeppo 🎉",
        )
    except Exception:
        logger.warning("could not notify user %s of paid withdrawal", result["user_id"])


@dp.callback_query(F.data.startswith("crypto_cancel:"))
async def handle_crypto_cancel(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return
    withdrawal_id = int(call.data.split(":")[1])
    result = db.admin_cancel_withdrawal(withdrawal_id)
    if result is None:
        await call.answer("Заявка уже обработана", show_alert=True)
        return
    try:
        await call.message.edit_text(
            _withdrawal_text(withdrawal_id, result["card_count"], result["gram_amount"],
                              result["wallet_address"], "❌ <b>Отменено</b>"),
            parse_mode="HTML",
        )
    except Exception:
        logger.warning("could not edit withdrawal message %s after cancel", withdrawal_id)
    await call.answer("Отменено")
    try:
        await bot.send_message(
            result["user_id"],
            f"❌ Твоя заявка на вывод {result['gram_amount']} GRAM отменена, карты вернулись в профиль.",
        )
    except Exception:
        logger.warning("could not notify user %s of cancelled withdrawal", result["user_id"])


# ---------------------------------------------------------------------------
# Inline sharing — tapping "Поделиться" in the webapp calls tg.switchInlineQuery(),
# which opens Telegram's native "send to..." chat picker. Whoever the player picks
# gets this card sent straight into that chat — no copy/forward step needed.
# Requires inline mode to be turned on for the bot once via @BotFather (/setinline).
# ---------------------------------------------------------------------------

@dp.inline_query()
async def handle_inline_share(inline_query: InlineQuery):
    query = inline_query.query or ""
    if not query.startswith("share:"):
        await inline_query.answer([], cache_time=1, is_personal=True)
        return
    try:
        user_card_id = int(query[len("share:"):])
    except ValueError:
        await inline_query.answer([], cache_time=1, is_personal=True)
        return

    uc = db.get_user_card(user_card_id)
    if uc is None or uc["user_id"] != inline_query.from_user.id:
        # not their card (or it doesn't exist) — return nothing rather than leak it
        await inline_query.answer([], cache_time=1, is_personal=True)
        return

    name = uc["name"] or "картинка"
    photo_url = f"{WEBAPP_ORIGIN}/static/cards/{uc['filename']}"
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref{inline_query.from_user.id}"
    result = InlineQueryResultPhoto(
        id=str(user_card_id),
        photo_url=photo_url,
        thumbnail_url=photo_url,
        caption=f"Смотри что мне выпало «{name}» 🎁 Залетай в Peeppo и фарми карты → {ref_link}",
    )
    try:
        await inline_query.answer([result], cache_time=1, is_personal=True)
    except Exception:
        logger.warning("could not answer inline share query for card %s", user_card_id)


# ---------------------------------------------------------------------------
# Referral race — /ref shows a top-5 leaderboard, counting only referrals who've
# farmed at least one card AND joined PUBLIC_CHAT (see database.py's
# get_unverified_ref_candidates()/mark_chat_verified()/get_ref_leaderboard()). A
# one-time scheduled message re-posts the same leaderboard at REF_RACE_ANNOUNCE_AT.
# ---------------------------------------------------------------------------

async def sync_referral_chat_verification():
    """Spends one getChatMember call per not-yet-verified, already-farmed referral to
    check if they've joined PUBLIC_CHAT, and flags the ones who have. Cheap to call
    often — already-verified rows are never rechecked."""
    for user_id in db.get_unverified_ref_candidates():
        try:
            member = await bot.get_chat_member(PUBLIC_CHAT, user_id)
            if member.status in ("member", "administrator", "creator"):
                db.mark_chat_verified(user_id)
        except Exception:
            # not in the chat (yet), or we can't see them — just retried next time
            pass


def format_ref_leaderboard(rows: list[dict]) -> str:
    if not rows:
        return (
            f"🏆 Топ-5 по рефералам\n\n"
            f"Пока пусто — рефералы засчитываются, когда друг зашёл в бота по твоей "
            f"ссылке, вступил в {PUBLIC_CHAT} и сделал первый фарм карты."
        )
    medals = ["🥇", "🥈", "🥉", "4.", "5."]
    lines = ["🏆 Топ-5 по рефералам:", ""]
    for i, row in enumerate(rows):
        name = f"@{row['username']}" if row["username"] else (row["first_name"] or f"id{row['telegram_id']}")
        lines.append(f"{medals[i]} {name} — {row['n']}")
    return "\n".join(lines)


@dp.message(Command("ref"))
async def handle_ref_command(message: Message):
    """Works the same in PUBLIC_CHAT or in a private DM with the bot — no chat-type
    filter, and Telegram always delivers slash commands to bots regardless of the
    bot's privacy-mode setting."""
    await sync_referral_chat_verification()
    rows = db.get_ref_leaderboard(5, exclude_id=int(ADMIN_ID) if ADMIN_ID else None)
    await message.answer(format_ref_leaderboard(rows))


async def ref_race_scheduler():
    """Background loop living for the lifetime of the bot process: once real time
    passes REF_RACE_ANNOUNCE_AT, posts the leaderboard to PUBLIC_CHAT exactly once
    (guarded by db.has_ref_race_been_announced(), same idempotency pattern as
    check_hundred_club/hundred_club) and keeps looping harmlessly forever after."""
    logger.info("referral race scheduler started")
    while True:
        try:
            if not db.has_ref_race_been_announced() and datetime.now(ZoneInfo("Europe/Moscow")) >= REF_RACE_ANNOUNCE_AT:
                await sync_referral_chat_verification()
                rows = db.get_ref_leaderboard(5, exclude_id=int(ADMIN_ID) if ADMIN_ID else None)
                text = "🏁 Реферальная гонка завершена!\n\n" + format_ref_leaderboard(rows)
                try:
                    await bot.send_message(PUBLIC_CHAT, text)
                except Exception:
                    logger.warning("could not post ref race results to %s", PUBLIC_CHAT)
                db.mark_ref_race_announced()
        except Exception:
            logger.exception("ref race scheduler iteration failed")
        await asyncio.sleep(300)


async def main():
    db.init_db()
    logger.info("Peeppo bot starting (polling)...")
    await check_hundred_club()  # in case we already had 100+ users before this deploy
    asyncio.create_task(gem_drop_scheduler())
    asyncio.create_task(giveaway_scheduler())
    asyncio.create_task(ref_race_scheduler())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
