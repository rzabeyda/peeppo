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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import (
    ReplyKeyboardRemove,
    BotCommand,
    CallbackQuery,
    ForceReply,
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

# Telegram/its WebView caches the mini-app HTML by exact URL, same as it cached card
# images earlier -- bump this on every real webapp/index.html deploy so the "Open app"
# button forces a fresh fetch instead of reusing a stale cached page.
WEBAPP_VERSION = "4"

# The admin's own real @handle should never leak in anything posted publicly (giveaway/
# contest winner announcements, share-to-chat results, Aviator chat messages) -- it's
# shown as a generic "Бот" label there instead. Never applied to private replies
# (admin commands like /finduser, /addgem) since only the admin themselves sees those.
ADMIN_USERNAME_MASK = "rzabeyda"


def _mask_username(username: str | None) -> str | None:
    if username and username.lower() == ADMIN_USERNAME_MASK:
        return "Бот"
    return username


def _who_label(username: str | None, first_name: str | None, telegram_id: int) -> str:
    """"@handle" (or first_name/id fallback) for a winner/player shown in a public
    announcement -- masked through _mask_username() first."""
    username = _mask_username(username)
    if username == "Бот":
        return "Бот"
    return f"@{username}" if username else (first_name or str(telegram_id))

AVIATOR_MAX_CONCURRENT_PER_CHAT = 5  # see database.count_active_aviator_rounds_in_chat -- caps how many /go tickers can hammer edit_text in the same chat at once

# asyncio.create_task() only holds a WEAK reference in the event loop -- a task with
# no other strong reference anywhere can get garbage-collected mid-flight at any time,
# silently killing it with no exception and no log line. This is very likely the REAL
# cause of "ракетка зависает" (confirmed happening even with exactly one solo player
# and zero other chat activity, which rules out flood control as the only cause):
# keep every in-flight aviator ticker task referenced here until it finishes, per the
# standard asyncio guidance (https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task).
_active_aviator_tasks: set[asyncio.Task] = set()
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
    kb.button(text="Фармить", web_app=WebAppInfo(url=f"{WEBAPP_URL}?v={WEBAPP_VERSION}"))
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
            "Жми Фарм — собирай карточки, играй в PvP, крафти, обменивайся или продавай "
            "на рынке. Обменивай Diamond карты на Telegram Stars."
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
    lines = [
        "👑 <b>Админ-панель Peeppo</b>\n",
        f"Юзеры: <b>{stats['users']}</b>",
        f"Сегодня: <b>{stats['active_today']}</b>",
        f"Фармили сегодня: <b>{stats['farmers_today']}</b>",
        f"Карты: <b>{stats['total_farmed']}</b>",
        f"Фарм: <b>{stats['farms_pressed']}</b>",
        f"Кейсы: <b>{stats['cases_bought']}</b>",
        f"Крафт: <b>{stats['cards_crafted']}</b>",
        f"Эволюция: <b>{stats['cards_evolved']}</b>",
        f"Стейки: <b>{stats['cards_staked']}</b>",
        "",
        "📊 <b>Статистика казны по всем играм</b>\n",
    ]
    all_stats = db.get_all_house_stats()
    for key in ("mines", "redblack", "aviator", "poker"):
        lines.append(_house_stats_line(key, all_stats[key]))
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("pvpbot"))
async def handle_pvp_test_bot(message: Message):
    """Admin-only: makes a fake bot account join the current open PvP lobby round
    with a free card, so a 2-player round (and the reveal animation) can be triggered
    for testing without a second real account."""
    if not _is_admin(message.from_user.id):
        return
    try:
        db.join_pvp_test_bot()
    except Exception as e:
        await message.answer(f"Не получилось: {e}")


@dp.message(Command("minesstats"))
async def handle_mines_stats(message: Message):
    if not _is_admin(message.from_user.id):
        return
    s = db.get_mines_house_stats()
    if not s["rounds"]:
        await message.answer("Минные поля ещё никто не играл.")
        return
    sign = "🟢 в плюсе" if s["profit"] > 0 else ("🔴 в минусе" if s["profit"] < 0 else "⚪ ровно")
    await message.answer(
        "💣 <b>Минные поля — статистика казны</b>\n\n"
        f"Раундов сыграно: <b>{s['rounds']}</b>\n"
        f"Поставлено: <b>{s['wagered']}</b> 💎\n"
        f"Выплачено: <b>{s['paid']}</b> 💎\n"
        f"Итог: {sign} на <b>{abs(s['profit'])}</b> 💎\n"
        f"Фактический RTP: <b>{s['effective_rtp']}%</b> (целевой — 97%)",
        parse_mode="HTML",
    )


_HOUSE_STATS_LABELS = {
    "mines": ("💣", "Минные поля", 97),
    "redblack": ("🔴⚫", "Red&Black", 100),
    "aviator": ("🚀", "Ракетка", 97),
    "poker": ("🃏", "Покер", None),
}


def _house_stats_line(key: str, s: dict) -> str:
    emoji, label, target = _HOUSE_STATS_LABELS[key]
    if not s["rounds"]:
        return f"{emoji} <b>{label}</b> — ещё не играли"
    return f"{emoji} <b>{label}</b> — RTP {s['effective_rtp']}%"


@dp.message(Command("housestats"))
async def handle_house_stats(message: Message):
    if not _is_admin(message.from_user.id):
        return
    all_stats = db.get_all_house_stats()
    lines = ["📊 <b>Статистика казны по всем играм</b>\n"]
    for key in ("mines", "redblack", "aviator", "poker"):
        lines.append(_house_stats_line(key, all_stats[key]))
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("topstakers"))
async def handle_top_stakers(message: Message):
    if not _is_admin(message.from_user.id):
        return
    rows = db.get_top_stakers(10)
    if not rows:
        await message.answer("Пока никто ничего не заработал на стейкинге.")
        return
    lines = ["📊 <b>Топ-10 по стейкингу</b> (всего заработано гемов)\n"]
    for i, r in enumerate(rows, 1):
        name = f"@{r['username']}" if r['username'] else (r['first_name'] or f"id{r['telegram_id']}")
        lines.append(f"{i}. {name} — <b>{r['staking_gems_earned']}</b> 💎")
    await message.answer("\n".join(lines), parse_mode="HTML")


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


GEM_DROP_AMOUNT = 25

# Auto-scheduler: fires roughly once every hour, only during the active window
# 06:00-02:00 Tallinn time (quiet 02:00-06:00) -- the window WRAPS past midnight, see
# the hour check below. GEM_DROP_MIN_GAP_SECONDS guards against firing a second drop
# too soon if the bot process restarts a few times in a row (e.g. during a deploy) --
# kept a bit below GEM_DROP_INTERVAL_SECONDS so the +/-180s jitter on the sleep below
# never causes a legitimate hourly drop to be skipped.
GEM_DROP_TZ = ZoneInfo("Europe/Tallinn")
GEM_DROP_START_HOUR = 6   # window opens 06:00
GEM_DROP_END_HOUR = 2     # window closes 02:00 (next day) -- start > end means it wraps
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
    hour, round the clock — if no drop went out too recently — posts an automatic
    GEM_DROP_AMOUNT-gem drop into PUBLIC_CHAT."""
    logger.info("gem drop scheduler started (06:00-02:00 Tallinn, ~every 1h, %d gems)", GEM_DROP_AMOUNT)
    while True:
        try:
            now_local = datetime.now(GEM_DROP_TZ)
            if GEM_DROP_START_HOUR <= GEM_DROP_END_HOUR:
                in_window = GEM_DROP_START_HOUR <= now_local.hour < GEM_DROP_END_HOUR
            else:
                # Wrapping window (e.g. 06:00-02:00): active from start-hour through
                # midnight, then from midnight up to (not including) end-hour.
                in_window = now_local.hour >= GEM_DROP_START_HOUR or now_local.hour < GEM_DROP_END_HOUR
            if in_window:
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
        f"{_who_label(w['username'], w['first_name'], w['telegram_id'])} — {w['count']} шт."
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


def _number_giveaway_text(number: int, name: str, rarity: str, hours: float, entry_count: int) -> str:
    hours_label = f"{int(hours)}ч" if float(hours).is_integer() else f"{hours:g}ч"
    return (
        f"🎉 Розыгрыш карты №{number}!\n\n"
        f"«{name or rarity}» ({rarity.upper()})\n"
        f"Жми «Участвовать» — итоги через {hours_label}\n"
        f"Участники: {entry_count}"
    )


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
    text = _number_giveaway_text(card["number"], card["name"], card["rarity"], hours, 0)
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
        giveaway = db.get_number_giveaway(giveaway_id)
        if giveaway and giveaway.get("message_id"):
            try:
                created = datetime.fromisoformat(giveaway["created_at"])
                draw_at = datetime.fromisoformat(giveaway["draw_at"])
                hours = (draw_at - created).total_seconds() / 3600
                count = db.count_number_giveaway_entries(giveaway_id)
                text = _number_giveaway_text(
                    giveaway["number"], giveaway["card_name"], giveaway["card_rarity"], hours, count
                )
                kb = InlineKeyboardBuilder()
                kb.button(text="Участвовать", callback_data=f"numgiveaway_join:{giveaway_id}")
                await bot.edit_message_text(
                    chat_id=PUBLIC_CHAT, message_id=giveaway["message_id"], text=text,
                    reply_markup=kb.as_markup(),
                )
            except Exception:
                logger.warning("could not update participant count on number giveaway %s post", giveaway_id)
    elif status == "already_joined":
        await call.answer("Ты уже участвуешь", show_alert=True)
    elif status == "is_admin":
        await call.answer("Нельзя участвовать в своём же розыгрыше", show_alert=True)
    elif status == "drawn":
        await call.answer("Розыгрыш уже завершён", show_alert=True)
    else:
        await call.answer("Розыгрыш не найден", show_alert=True)


def _card_batch_giveaway_text(total_cards: int, hours: float, entry_count: int) -> str:
    hours_label = f"{int(hours)}ч" if float(hours).is_integer() else f"{hours:g}ч"
    return (
        f"\U0001F389 Розыгрыш {total_cards} карт!\n\n"
        f"Разыгрываются между всеми участниками\n"
        f"Жми «Участвовать» -- итоги через {hours_label}\n"
        f"Участники: {entry_count}"
    )


def _giveaway_reminder_when(kind: str) -> str:
    return "остался 1 час" if kind == "1h" else "осталось 5 минут"


def _number_giveaway_reminder_text(number: int, name: str, rarity: str, kind: str) -> str:
    return (
        f"\u23F0 До розыгрыша карты №{number} «{name or rarity}» ({rarity.upper()}) "
        f"{_giveaway_reminder_when(kind)}!\n"
        f"Успей нажать «Участвовать» под постом розыгрыша выше \U0001F446"
    )


def _card_batch_giveaway_reminder_text(total_cards: int, kind: str) -> str:
    return (
        f"\u23F0 До розыгрыша {total_cards} карт {_giveaway_reminder_when(kind)}!\n"
        f"Успей нажать «Участвовать» под постом розыгрыша выше \U0001F446"
    )


@dp.message(Command("cardsgiveaway"))
async def handle_admin_cards_giveaway(message: Message):
    """Admin-only: /cardsgiveaway [часов] -- snapshots EVERY non-diamond card the
    admin currently owns (that isn't already busy) and posts one shared \"Участвовать\"
    giveaway into PUBLIC_CHAT for the given duration (default 1 hour). At draw time
    each card independently goes to a random participant -- same person can win
    several cards, or none; see create_card_batch_giveaway/draw_card_batch_giveaway
    in database.py for exactly how the locking and the draw work."""
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    try:
        hours = float(parts[1]) if len(parts) > 1 else 1.0
    except ValueError:
        await message.answer("Формат: /cardsgiveaway [часов], например /cardsgiveaway 3")
        return

    try:
        result = db.create_card_batch_giveaway(message.from_user.id, hours)
    except db.CardBatchGiveawayError as e:
        await message.answer(f"Не удалось разыграть: {e}")
        return

    batch_id = result["batch_id"]
    total_cards = len(result["cards"])
    kb = InlineKeyboardBuilder()
    kb.button(text="Участвовать", callback_data=f"cardsgiveaway_join:{batch_id}")
    text = _card_batch_giveaway_text(total_cards, hours, 0)
    try:
        sent = await bot.send_message(PUBLIC_CHAT, text, reply_markup=kb.as_markup())
    except Exception:
        await message.answer(f"Не удалось опубликовать в {PUBLIC_CHAT} -- бот точно там состоит?")
        return
    db.set_card_batch_giveaway_message(batch_id, sent.chat.id, sent.message_id)
    await message.answer(
        f"Розыгрыш {total_cards} карт (все кроме diamond) опубликован в {PUBLIC_CHAT}. Итоги через {_format_hours(hours)}."
    )


@dp.callback_query(F.data.startswith("cardsgiveaway_join:"))
async def handle_card_batch_giveaway_join(call: CallbackQuery):
    batch_id = int(call.data.split(":")[1])
    db.get_or_create_user(
        telegram_id=call.from_user.id,
        username=call.from_user.username,
        first_name=call.from_user.first_name,
        ref_by=None,
    )
    status = db.join_card_batch_giveaway(batch_id, call.from_user.id)
    if status == "joined":
        await call.answer("Ты участвуешь! Удачи \U0001F340", show_alert=True)
        giveaway = db.get_card_batch_giveaway(batch_id)
        if giveaway and giveaway.get("message_id"):
            try:
                created = datetime.fromisoformat(giveaway["created_at"])
                draw_at = datetime.fromisoformat(giveaway["draw_at"])
                hours = (draw_at - created).total_seconds() / 3600
                count = db.count_card_batch_giveaway_entries(batch_id)
                text = _card_batch_giveaway_text(giveaway["total_cards"], hours, count)
                kb = InlineKeyboardBuilder()
                kb.button(text="Участвовать", callback_data=f"cardsgiveaway_join:{batch_id}")
                await bot.edit_message_text(
                    chat_id=giveaway["chat_id"], message_id=giveaway["message_id"], text=text,
                    reply_markup=kb.as_markup(),
                )
            except Exception:
                logger.warning("could not update participant count on card batch giveaway %s post", batch_id)
    elif status == "already_joined":
        await call.answer("Ты уже участвуешь", show_alert=True)
    elif status == "is_admin":
        await call.answer("Нельзя участвовать в своём же розыгрыше", show_alert=True)
    elif status == "drawn":
        await call.answer("Розыгрыш уже завершён", show_alert=True)
    else:
        await call.answer("Розыгрыш не найден", show_alert=True)


async def _announce_card_batch_giveaway_result(giveaway: dict, result: dict):
    """Builds a PER-WINNER summary (name -> how many cards, incl. rarity breakdown)
    instead of one line per card -- with big batches (hundreds of cards) a one-line-
    per-card message blows past Telegram's 4096-char limit, and send_message/
    edit_message_text then silently fail (caught below, only logged) -- cards were
    genuinely handed out but the chat never finds out. A compact per-winner summary
    stays well under the limit at any realistic participant count, and chunks itself
    into multiple messages as a last-resort safety net if it somehow still doesn't."""
    results = result["results"]
    by_winner: dict[int, dict] = {}
    not_transferred = 0
    for r in results:
        card = r["card"]
        if r["winner"] and r["transferred"]:
            w = r["winner"]
            who = _who_label(w["username"], w["first_name"], w["telegram_id"])
            bucket = by_winner.setdefault(w["telegram_id"], {"who": who, "total": 0, "by_rarity": {}})
            bucket["total"] += 1
            rarity = (card["rarity"] or "?").upper()
            bucket["by_rarity"][rarity] = bucket["by_rarity"].get(rarity, 0) + 1
        elif r["winner"] and not r["transferred"]:
            not_transferred += 1
    if by_winner:
        ranked = sorted(by_winner.values(), key=lambda b: -b["total"])
        lines = []
        for b in ranked:
            breakdown = ", ".join(f"{n}×{rarity}" for rarity, n in b["by_rarity"].items())
            lines.append(f"{b['who']} — {b['total']} карт ({breakdown})")
        if not_transferred:
            lines.append(f"\n(не выдано: {not_transferred} карт(ы) — были заняты/недоступны на момент розыгрыша)")
        header = (
            f"\U0001F389 Розыгрыш {len(results)} карт завершён!\n\n"
            f"Участников: {result['total_entries']}\n\n"
        )
        chunks = []
        current = header
        for line in lines:
            if len(current) + len(line) + 1 > 3800:
                chunks.append(current)
                current = ""
            current += line + "\n"
        if current.strip():
            chunks.append(current)
    else:
        chunks = [f"\U0001F389 Розыгрыш {len(results)} карт завершён -- участников не набралось, увы."]
    chat_id = giveaway.get("chat_id") or PUBLIC_CHAT
    if giveaway.get("message_id"):
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=giveaway["message_id"], text=chunks[0])
        except Exception:
            logger.warning("could not edit original card batch giveaway %s post", giveaway["id"])
    for chunk in chunks:
        try:
            await bot.send_message(chat_id, chunk)
        except Exception:
            logger.warning("could not announce card batch giveaway %s result", giveaway["id"])


async def _announce_number_giveaway_result(giveaway: dict, result: dict):
    card = result["card"]
    if result["winner"] and result["transferred"]:
        w = result["winner"]
        who = _who_label(w["username"], w["first_name"], w["telegram_id"])
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
    # Edit the original post so it shows the final state in place, AND send a fresh
    # message with the same text — a long giveaway (hours) can scroll the original post
    # way up in chat history by the time it draws, so the edit alone is easy to miss;
    # the new message guarantees the result actually surfaces where people are looking.
    if giveaway.get("message_id"):
        try:
            await bot.edit_message_text(chat_id=PUBLIC_CHAT, message_id=giveaway["message_id"], text=text)
        except Exception:
            logger.warning("could not edit original number giveaway %s post", giveaway["id"])
    try:
        await bot.send_message(PUBLIC_CHAT, text)
    except Exception:
        logger.warning("could not announce number giveaway %s result", giveaway["id"])


async def _announce_giveaway_result(giveaway: dict, result: dict):
    winners = result["winners"]
    if winners:
        names = ", ".join(
            _who_label(w["username"], w["first_name"], w["telegram_id"])
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
            for giveaway in db.get_due_card_batch_giveaways():
                result = db.draw_card_batch_giveaway(giveaway["id"])
                await _announce_card_batch_giveaway_result(giveaway, result)
        except Exception:
            logger.exception("giveaway scheduler iteration failed")
        await asyncio.sleep(300)


async def giveaway_reminder_scheduler():
    """Background loop living for the lifetime of the bot process: every 60s, posts a
    "1 hour left" / "5 minutes left" ping into PUBLIC_CHAT (or the batch's own chat_id)
    for every still-open chat card giveaway -- /numbergiveaway and /cardsgiveaway --
    that just crossed that threshold. Each reminder fires exactly once per giveaway
    (see database.py's reminder_1h_sent/reminder_5m_sent + get_*_needing_reminder()).
    Polls far more often than the main giveaway_scheduler() (300s) specifically so the
    5-minute mark doesn't get skipped over between checks. A failed send is NOT marked
    as sent, so it's retried on the next poll instead of silently going missing."""
    logger.info("giveaway reminder scheduler started (~every 60s)")
    while True:
        try:
            for kind in ("1h", "5m"):
                for giveaway in db.get_number_giveaways_needing_reminder(kind):
                    text = _number_giveaway_reminder_text(
                        giveaway["number"], giveaway["card_name"], giveaway["card_rarity"], kind
                    )
                    try:
                        await bot.send_message(PUBLIC_CHAT, text)
                    except Exception:
                        logger.warning("could not send %s reminder for number giveaway %s", kind, giveaway["id"])
                        continue
                    db.mark_number_giveaway_reminder_sent(giveaway["id"], kind)
                for giveaway in db.get_card_batch_giveaways_needing_reminder(kind):
                    text = _card_batch_giveaway_reminder_text(giveaway["total_cards"], kind)
                    chat_id = giveaway.get("chat_id") or PUBLIC_CHAT
                    try:
                        await bot.send_message(chat_id, text)
                    except Exception:
                        logger.warning("could not send %s reminder for card batch giveaway %s", kind, giveaway["id"])
                        continue
                    db.mark_card_batch_giveaway_reminder_sent(giveaway["id"], kind)
        except Exception:
            logger.exception("giveaway reminder scheduler iteration failed")
        await asyncio.sleep(60)


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
        _who_label(w["username"], w["first_name"], w["telegram_id"])
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


async def notify_admin_payment(tg_user, stars: int, description: str):
    """Best-effort ping to you (ADMIN_ID in .env) whenever anyone spends Telegram
    Stars in the bot -- gems or a rank, chat command or webapp, same as the existing
    new-user/withdrawal admin pings."""
    if not ADMIN_ID:
        return
    who = f"@{tg_user.username}" if tg_user.username else (tg_user.first_name or str(tg_user.id))
    try:
        await bot.send_message(int(ADMIN_ID), f"💰 {who} купил(а) {description} за {stars} ⭐")
    except Exception:
        logger.warning("could not notify admin of payment from %s", tg_user.id)


@dp.message(F.successful_payment)
async def handle_successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload
    stars = message.successful_payment.total_amount  # XTR has no cent multiplier -- this IS the Star count
    if payload.startswith("gems:"):
        _, uid_str, gems_str = payload.split(":")
        gems = int(gems_str)
        new_balance = db.add_gems(int(uid_str), gems)
        await message.answer(f"Зачислено {gems} 💎! Баланс: {new_balance} 💎")
        await notify_admin_payment(message.from_user, stars, f"{gems} гемов")
    elif payload.startswith("rank:"):
        _, uid_str, rank = payload.split(":")
        new_rank = db.set_purchased_rank(int(uid_str), rank)
        await message.answer(f"Ранг {new_rank.upper()} куплен! 🏆")
        await notify_admin_payment(message.from_user, stars, f"ранг {new_rank.upper()}")


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
# Diamond-for-Stars withdrawal — "Продать за Stars" (repurposes the old GRAM/crypto
# withdrawal plumbing; wallet_address is now unused/legacy — Stars go straight to the
# player's own Telegram account, nothing to collect from them). See database.py's
# request_crypto_withdrawal()/admin_pay_withdrawal()/admin_cancel_withdrawal() for
# the mechanics — the selected cards are held (voided) the instant a request is
# submitted, so they can't be double-spent while it's pending. Only ADMIN_ID can
# act on the request; the admin sends the Stars manually to the player outside this
# system and then taps "Оплатить", or taps "Отменить" to give the cards back.
# ---------------------------------------------------------------------------

def _withdrawal_text(withdrawal_id: int, card_count: int, gram_amount: int, wallet_address: str,
                      display_name: str = "", user_id: int | None = None, status_line: str = "") -> str:
    player_line = f"Игрок: {display_name} (id {user_id})\n\n" if display_name else ""
    text = (
        f"{player_line}"
        f"⭐ <b>Заявка на вывод Stars</b> #{withdrawal_id}\n\n"
        f"Карт Diamond: <b>{card_count}</b>\n"
        f"К выплате: <b>{gram_amount} Stars</b>"
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
        _withdrawal_text(withdrawal_id, card_count, gram_amount, wallet_address, display_name, user_id) +
        "\n\nОтправь Stars игроку вручную, затем нажми «Оплатить»."
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
    payer = db.get_user(result["user_id"])
    display_name = _display_name(payer["username"], payer["first_name"]) if payer else ""
    try:
        await call.message.edit_text(
            _withdrawal_text(withdrawal_id, result["card_count"], result["gram_amount"],
                              result["wallet_address"], display_name, result["user_id"], "✅ <b>Оплачено</b>"),
            parse_mode="HTML",
        )
    except Exception:
        logger.warning("could not edit withdrawal message %s after pay", withdrawal_id)
    await call.answer("Отмечено как оплачено")
    try:
        await bot.send_message(
            result["user_id"],
            f"⭐ Твоя заявка на вывод {result['gram_amount']} Stars оплачена! Спасибо, что играешь в Peeppo 🎉",
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
    payer = db.get_user(result["user_id"])
    display_name = _display_name(payer["username"], payer["first_name"]) if payer else ""
    try:
        await call.message.edit_text(
            _withdrawal_text(withdrawal_id, result["card_count"], result["gram_amount"],
                              result["wallet_address"], display_name, result["user_id"], "❌ <b>Отменено</b>"),
            parse_mode="HTML",
        )
    except Exception:
        logger.warning("could not edit withdrawal message %s after cancel", withdrawal_id)
    await call.answer("Отменено")
    try:
        await bot.send_message(
            result["user_id"],
            f"❌ Твоя заявка на вывод {result['gram_amount']} Stars отменена, карты вернулись в профиль.",
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
# "Поделиться" share buttons in the Игры tab (PvP / Ракетка / Red&Black) -- the player
# taps Share on a round they just played, api.py verifies the round belongs to them
# and re-derives the outcome server-side (never trusts the client's own numbers), then
# one of these posts a single message into PUBLIC_CHAT. Each returns True/False so the
# endpoint can tell the player whether it actually went through.
# ---------------------------------------------------------------------------

def _display_name(username: str | None, first_name: str | None) -> str:
    username = _mask_username(username)
    if username == "Бот":
        return "Бот"
    return f"@{username}" if username else (first_name or "Игрок")


async def share_redblack_result(round_id: int) -> bool:
    round_row = db.get_redblack_round(round_id)
    if round_row is None:
        return False
    name = _display_name(round_row.get("username"), round_row.get("first_name"))
    color = "🔴 Красное" if round_row["result"] == "red" else "⚫ Чёрное"
    if round_row["won"]:
        profit = round_row["payout"] - round_row["bet"]
        text = f"🎲 {name} сыграл в Red&Black — выпало {color}, угадал и забрал +{profit} 💎!"
    else:
        text = f"🎲 {name} сыграл в Red&Black — выпало {color}, не угадал и потерял {round_row['bet']} 💎"
    try:
        await bot.send_message(PUBLIC_CHAT, text)
        return True
    except Exception:
        logger.warning("could not share redblack round %s", round_id)
        return False


async def share_mines_result(round_id: int) -> bool:
    round_row = db.get_mines_round(round_id)
    if round_row is None or round_row["status"] not in ("won", "lost"):
        return False
    name = _display_name(round_row.get("username"), round_row.get("first_name"))
    if round_row["status"] == "won":
        profit = round_row["payout"] - round_row["bet"]
        text = (
            f"💣 {name} сыграл в Минные поля ({round_row['mine_count']} мин) — забрал на "
            f"{round_row['cashout_multiplier']:.2f}x, выигрыш +{profit} 💎!"
        )
    else:
        text = f"💣 {name} сыграл в Минные поля ({round_row['mine_count']} мин) — подорвался, проигрыш {round_row['bet']} 💎"
    try:
        await bot.send_message(PUBLIC_CHAT, text)
        return True
    except Exception:
        logger.warning("could not share mines round %s", round_id)
        return False


_POKER_CATEGORY_LABEL = {
    "royal_flush": "ROYAL FLUSH", "five_of_a_kind": "5 OF A KIND", "straight_flush": "STR FLUSH",
    "four_kind": "4 OF A KIND", "full_house": "FULL HOUSE", "flush": "FLUSH", "straight": "STRAIGHT",
    "three_kind": "3 OF A KIND", "two_pair": "2 PAIRS", "jacks_or_better": "JACKS OR BETTER", "nothing": "MISS",
}  # mirrors webapp/index.html's POKER_CATEGORY_LABEL


async def share_poker_result(round_id: int) -> bool:
    round_row = db.get_poker_round(round_id)
    # "won" is included (not just "collected") so the player can share straight from the
    # double-up screen before tapping "Забрать" -- gems are already credited live at
    # draw/gamble time regardless, so the numbers are accurate either way.
    if round_row is None or round_row["status"] not in ("won", "collected", "busted"):
        return False
    name = _display_name(round_row.get("username"), round_row.get("first_name"))
    label = _POKER_CATEGORY_LABEL.get(round_row["category"], round_row["category"] or "MISS")
    net = (round_row["current_payout"] or 0) - round_row["bet"]
    if round_row["status"] in ("won", "collected") and net >= 0:
        ladder_note = f" (лесенка x{round_row['gamble_count']})" if round_row["gamble_count"] else ""
        text = f"🃏 {name} сыграл в Покер — {label}{ladder_note}, забрал +{net} 💎!"
    else:
        text = f"🃏 {name} сыграл в Покер — {label}, сгорело {round_row['bet']} 💎"
    try:
        await bot.send_message(PUBLIC_CHAT, text)
        return True
    except Exception:
        logger.warning("could not share poker round %s", round_id)
        return False


async def share_aviator_result(round_id: int) -> bool:
    round_row = db.get_aviator_round(round_id)
    if round_row is None or round_row["status"] not in ("won", "lost"):
        return False
    name = _display_name(round_row.get("username"), round_row.get("first_name"))
    if round_row["status"] == "won":
        payout = int(round(round_row["bet"] * round_row["cashout_multiplier"]))
        text = (
            f"🚀 {name} сыграл в Ракетку — забрал на {round_row['cashout_multiplier']:.2f}x, "
            f"выигрыш +{payout - round_row['bet']} 💎!"
        )
    else:
        text = f"🚀 {name} сыграл в Ракетку — улетела на {round_row['crash_point']:.2f}x, проигрыш {round_row['bet']} 💎"
    try:
        await bot.send_message(PUBLIC_CHAT, text)
        return True
    except Exception:
        logger.warning("could not share aviator round %s", round_id)
        return False


async def share_pvp_result(round_id: int) -> bool:
    round_row = db.get_last_resolved_pvp_round(round_id)
    if round_row is None:
        return False
    text = (
        f"⚔️ Розыгрыш PvP: {round_row['winner_name']} забрал банк — "
        f"{round_row['total_cards']} карт у {round_row['total_players']} игроков!"
    )
    try:
        await bot.send_message(PUBLIC_CHAT, text)
        return True
    except Exception:
        logger.warning("could not share pvp round %s", round_id)
        return False


async def send_pvp_invite(name: str) -> bool:
    """"Позвать игрока" button in the PvP lobby -- one ping into PUBLIC_CHAT inviting
    others to join the open bank. Cooldown is enforced server-side by
    db.try_pvp_invite() before this is ever called."""
    text = f"⚔️ {name} зовёт в PvP"
    try:
        await bot.send_message(PUBLIC_CHAT, text)
        return True
    except Exception:
        logger.warning("could not send pvp invite for %s", name)
        return False


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


GEMS_PER_STAR = 10  # keep in sync with api.py's GEMS_PER_STAR


async def _send_gems_invoice(chat_id: int, user_id: int, gems: int) -> str | None:
    """Validates gems and posts the Stars invoice into chat_id. Returns an error
    string to show the player if the amount is invalid, None on success."""
    if gems <= 0 or gems % GEMS_PER_STAR != 0:
        return f"Гемы должны быть положительным числом, кратным {GEMS_PER_STAR} (Stars не бывают дробными)"
    stars = gems // GEMS_PER_STAR
    await bot.send_invoice(
        chat_id=chat_id,
        title="Гемы Peeppo",
        description=f"{gems} \U0001F48E гемов — трать их на рынке картинок",
        payload=f"gems:{user_id}:{gems}",
        provider_token="",  # empty provider_token is required for Telegram Stars
        currency="XTR",
        prices=[LabeledPrice(label=f"{gems} гемов", amount=stars)],
    )
    return None


@dp.message(Command("buy"), F.chat.type.in_({"group", "supergroup", "private"}))  # теперь работает и в личке с ботом, не только в групповом чате
async def handle_buy_command(message: Message):
    """/buy [гемы] -- posts a native Telegram Stars invoice right in this chat, same
    pricing/payload convention as the webapp's buy-gems flow (api.py's GEMS_PER_STAR,
    kept in sync by hand since bot.py can't import api.py -- api.py already imports
    bot.py, so the reverse would be circular). handle_successful_payment() below
    credits the gems the same way no matter which flow created the invoice.

    Requires the amount up front (no ForceReply follow-up step -- ForceReply leaves
    the chat's compose box permanently pinned to "reply to this" until the user
    explicitly dismisses it, which read as the bot randomly popping up a message every
    time the chat was reopened). reply_markup=ReplyKeyboardRemove() on the usage
    message also clears any ForceReply prompt still stuck from before this fix."""
    db.get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        ref_by=None,
    )
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(
            f"Формат: /buy [гемы], например /buy 1000 (кратно {GEMS_PER_STAR}, {GEMS_PER_STAR} гемов = 1 Star)",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    try:
        gems = int(parts[1])
    except ValueError:
        await message.answer(f"Формат: /buy [гемы], например /buy 1000 (кратно {GEMS_PER_STAR}, {GEMS_PER_STAR} гемов = 1 Star)")
        return
    error = await _send_gems_invoice(message.chat.id, message.from_user.id, gems)
    if error:
        await message.answer(error)


def _help_text() -> str:
    """Shared with handle_help_command() (/help, on demand) and
    daily_help_broadcast_scheduler() (the same list, posted to PUBLIC_CHAT once a day)
    -- one place to update whenever a chat command is added/changed."""
    return (
        "\U0001F4CB <b>Команды в чате</b>\n\n"
        "\U0001F48E /bank — проверить баланс гемов\n\n"
        "\U0001F534\U000026AB /redblack [ставка] (или /rb)\n"
        "\U0001F680 /go [ставка] — игры ракетка\n\n"
        "\U0001F4B3 /buy [гемы] — купить гемы за TS"
    )


@dp.message(Command("help"))
async def handle_help_command(message: Message):
    """/help -- lists the chat-only commands (works in PUBLIC_CHAT and in a private DM
    alike, same as the commands it lists)."""
    await message.answer(_help_text(), parse_mode="HTML")


HELP_BROADCAST_TZ = ZoneInfo("Europe/Tallinn")  # same convention as gem drops/daily bonus rollover
HELP_BROADCAST_HOUR = 18


async def daily_help_broadcast_scheduler():
    """Background loop living for the lifetime of the bot process: once a day, during
    the HELP_BROADCAST_HOUR:00 Tallinn hour, posts the /help command list into
    PUBLIC_CHAT as a reminder. Idempotent via db.daily_command_broadcast (one row per
    calendar date already posted) so the loop's own 10-minute polling granularity, or
    a restart landing inside that same hour, can never double-post."""
    logger.info("daily help broadcast scheduler started (%02d:00 Europe/Tallinn)", HELP_BROADCAST_HOUR)
    while True:
        try:
            now_local = datetime.now(HELP_BROADCAST_TZ)
            if now_local.hour == HELP_BROADCAST_HOUR:
                today_str = now_local.date().isoformat()
                if not db.has_daily_command_broadcast_posted(today_str):
                    try:
                        await bot.send_message(PUBLIC_CHAT, _help_text(), parse_mode="HTML")
                        db.mark_daily_command_broadcast_posted(today_str)
                        logger.info("posted daily command list to %s", PUBLIC_CHAT)
                    except Exception:
                        logger.warning("could not post daily command list to %s", PUBLIC_CHAT)
        except Exception:
            logger.exception("daily help broadcast scheduler iteration failed")
        await asyncio.sleep(600)  # check every 10 min -- plenty of margin inside the 1h window


@dp.message(Command("bank"))
async def handle_bank_command(message: Message):
    """/bank — check your own gem balance. Same no-chat-type-filter deal as /ref:
    works in PUBLIC_CHAT and in a private DM with the bot alike."""
    db.get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        ref_by=None,
    )
    gems = db.get_gems(message.from_user.id)
    await message.answer(f"💎 Баланс: {gems} гемов")


@dp.message(Command("redblack", "rb"))
async def handle_redblack_command(message: Message):
    """/redblack [ставка] — starts a round of red/black (default REDBLACK_DEFAULT_BET
    gems if no amount given). Posts a Red/Black picker; the actual bet only gets
    placed once a button is tapped (handle_redblack_choice), so this command itself
    never touches the balance — it just checks it's enough and shows the buttons."""
    db.get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        ref_by=None,
    )
    parts = (message.text or "").split()
    if len(parts) > 1:
        try:
            bet = int(parts[1])
        except ValueError:
            await message.answer("Формат: /redblack [ставка] (или /rb), например /rb 100")
            return
        if bet < db.REDBLACK_MIN_BET:
            await message.answer(f"Минимальная ставка -- {db.REDBLACK_MIN_BET} гемов")
            return
    else:
        bet = db.REDBLACK_DEFAULT_BET

    gems = db.get_gems(message.from_user.id)
    if gems < bet:
        await message.answer(f"Не хватает гемов на ставку {bet} — на балансе {gems}. Проверь /bank")
        return

    kb = InlineKeyboardBuilder()
    kb.button(text="🔴 Red", callback_data=f"redblack:{message.from_user.id}:{bet}:red")
    kb.button(text="⚫ Black", callback_data=f"redblack:{message.from_user.id}:{bet}:black")
    kb.adjust(2)
    await message.answer(
        f"🔴⚫ <b>Красное/чёрное</b> — ставка {bet} гемов\nВыбирай: Red или Black?",
        parse_mode="HTML",
        reply_markup=kb.as_markup(),
    )


@dp.callback_query(F.data.startswith("redblack:"))
async def handle_redblack_choice(call: CallbackQuery):
    try:
        _, owner_id_s, bet_s, choice = call.data.split(":")
        owner_id = int(owner_id_s)
        bet = int(bet_s)
    except ValueError:
        await call.answer("Битая кнопка, начни заново через /redblack", show_alert=True)
        return
    if call.from_user.id != owner_id:
        await call.answer("Это не твоя игра — сделай свою ставку через /redblack", show_alert=True)
        return
    try:
        result = db.play_redblack(call.from_user.id, bet, choice)
    except db.InsufficientGems:
        await call.answer("Не хватает гемов на балансе", show_alert=True)
        return
    except db.RedBlackError as e:
        await call.answer(f"Ошибка: {e}", show_alert=True)
        return

    # The outcome above is already final and fair (computed the instant they tapped) —
    # this is purely a ~2s cosmetic reveal animation, never a delay on the actual RNG.
    await call.answer("🎲 Крутим...")
    picked_label = "🔴 Red" if choice == "red" else "⚫ Black"
    spin_frames = ["🔴⚫🔴⚫", "⚫🔴⚫🔴", "🔴⚫🔴⚫"]
    for frame in spin_frames:
        try:
            await call.message.edit_text(f"{frame}\n\nСтавка: {picked_label}, {bet} гемов\nКрутим...")
        except Exception:
            pass
        await asyncio.sleep(0.65)

    color_emoji = "🔴" if result["result"] == "red" else "⚫"
    if result["won"]:
        text = (
            f"{color_emoji} Выпало: <b>{result['result'].upper()}</b>!\n\n"
            f"🎉 Угадал! Выигрыш {result['payout']} гемов (баланс: {result['gems']})"
        )
    else:
        text = (
            f"{color_emoji} Выпало: <b>{result['result'].upper()}</b>\n\n"
            f"😔 Не повезло — потерял {bet} гемов (баланс: {result['gems']})"
        )

    # Clear the Red/Black buttons so this exact bet can't be replayed by tapping again —
    # an empty InlineKeyboardBuilder still needs to be sent explicitly (Telegram only
    # removes an existing keyboard when reply_markup is an EXPLICIT empty one, not when
    # the parameter is simply omitted).
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardBuilder().as_markup())
    except Exception:
        logger.warning("could not edit redblack result message for user %s", call.from_user.id)


def _player_label(username: str | None, first_name: str | None, telegram_id: int) -> str:
    """Display name for the Aviator chat messages -- username WITHOUT the @ prefix
    (unlike notify_admin_new_user's "@username" DM style) so it's readable as plain
    text in a group chat, falling back to first_name then the bare id."""
    return _mask_username(username) or first_name or str(telegram_id)


@dp.message(Command("go"))
async def handle_aviator_command(message: Message):
    """/go [ставка] -- starts an Aviator round (default AVIATOR_DEFAULT_BET gems if no
    amount given). The bet is placed and the (hidden) crash point drawn immediately by
    db.start_aviator() -- everything after that (the climbing multiplier + the
    "Забрать" button on each tick) is just a live cosmetic reveal of that already-
    decided outcome, same "decided the instant the bet is placed" philosophy as
    /redblack's coin flip. Works in PUBLIC_CHAT and in a private DM alike, same as
    /bank and /redblack."""
    db.get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        ref_by=None,
    )
    parts = (message.text or "").split()
    if len(parts) > 1:
        try:
            bet = int(parts[1])
        except ValueError:
            await message.answer("Формат: /go [ставка], например /go 85")
            return
        if bet < db.AVIATOR_MIN_BET:
            await message.answer(f"Минимальная ставка -- {db.AVIATOR_MIN_BET} гемов")
            return
    else:
        bet = db.AVIATOR_DEFAULT_BET

    active_in_chat = db.count_active_aviator_rounds_in_chat(message.chat.id)
    if active_in_chat >= AVIATOR_MAX_CONCURRENT_PER_CHAT:
        await message.answer(
            f"Сейчас в этом чате уже {active_in_chat} игр(ы) в ракетку одновременно -- "
            f"подожди немного, пока кто-то заберёт или улетит, и попробуй снова"
        )
        return

    try:
        started = db.start_aviator(message.from_user.id, bet)
    except db.InsufficientGems:
        gems = db.get_gems(message.from_user.id)
        await message.answer(f"Не хватает гемов на ставку {bet} -- на балансе {gems}. Проверь /bank")
        return
    except db.AviatorError as e:
        await message.answer(f"Ошибка: {e}")
        return

    round_id = started["round_id"]
    first_tick = db.AVIATOR_TICKS[0]
    player_label = _player_label(message.from_user.username, message.from_user.first_name, message.from_user.id)
    kb = InlineKeyboardBuilder()
    kb.button(text=f"\U0001F48E Забрать {first_tick:.2f}x", callback_data=f"aviator:{round_id}:{message.from_user.id}:{first_tick}")
    sent = await message.answer(
        f"\U0001F680 Полетели! Игрок: {player_label}, ставка {bet} гемов\n\n<b>{first_tick:.2f}x</b>",
        parse_mode="HTML",
        reply_markup=kb.as_markup(),
    )
    db.set_aviator_message(round_id, sent.chat.id, sent.message_id)
    _aviator_task = asyncio.create_task(run_aviator_round(round_id, message.from_user.id, bet, sent, player_label))
    _active_aviator_tasks.add(_aviator_task)
    _aviator_task.add_done_callback(_active_aviator_tasks.discard)


async def run_aviator_round(round_id: int, user_id: int, bet: int, sent_message: Message, player_label: str):
    """Background ticker for one /go round: walks AVIATOR_TICKS (past the first, which
    handle_aviator_command already displayed), editing sent_message with a fresh
    multiplier + a fresh "Забрать" button (baked with THAT tick's own multiplier) each
    step. Re-checks db.peek_aviator_crash() every step rather than trusting a value
    captured once at round start, so a cashout that lands mid-loop (resolved
    independently/atomically by handle_aviator_cashout) is noticed and this loop backs
    off immediately without clobbering the win message. player_label is only for
    display -- who this round's message belongs to, in a group chat where several
    people can have a /go running at once."""
    try:
        for m in db.AVIATOR_TICKS[1:]:
            await asyncio.sleep(1.5)  # slower tick = fewer edit_text calls/sec -- less chance of hitting Telegram flood control when several /go games run at once in the same chat
            crash_point = db.peek_aviator_crash(round_id)
            if crash_point is None:
                return  # already resolved (cashed out) by the callback handler
            if crash_point <= m:
                if db.mark_aviator_crashed(round_id):
                    try:
                        await sent_message.edit_text(
                            f"\U0001F4A5 {player_label}: улетела на <b>{crash_point:.2f}x</b>\n\nСтавка {bet} гемов сгорела",
                            parse_mode="HTML",
                            reply_markup=InlineKeyboardBuilder().as_markup(),
                        )
                    except Exception:
                        pass
                return
            kb = InlineKeyboardBuilder()
            kb.button(text=f"\U0001F48E Забрать {m:.2f}x", callback_data=f"aviator:{round_id}:{user_id}:{m}")
            try:
                await sent_message.edit_text(
                    f"\U0001F680 Летит... Игрок: {player_label}\n\n<b>{m:.2f}x</b>",
                    parse_mode="HTML", reply_markup=kb.as_markup()
                )
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except Exception as e:
                # Previously a silent pass -- meant a stuck/frozen animation left
                # ZERO trace in the logs, so every past freeze report was pure
                # guesswork. Now at least the exact reason (bad request, network
                # blip, message deleted, whatever) shows up for the next one.
                logger.warning("aviator tick edit failed for round %s at %.2fx: %r", round_id, m, e)
        # Reached the top of AVIATOR_TICKS without cashing out or crashing -- a chat
        # message can't animate forever, so the rocket is capped there: force the
        # player's cashout at the highest tick instead. This can only ever help them
        # (a forced WIN at the max multiplier shown), never hurt them.
        cap = db.AVIATOR_TICKS[-1]
        try:
            result = db.cashout_aviator(round_id, user_id, cap)
        except db.AviatorError:
            return  # a last-instant tap already resolved it first
        try:
            await sent_message.edit_text(
                f"\U0001F680 {player_label}: потолок {cap:.2f}x -- забрали автоматически!\n\n"
                f"\U0001F389 Выигрыш {result['payout']} гемов (баланс: {result['gems']})",
                parse_mode="HTML",
                reply_markup=InlineKeyboardBuilder().as_markup(),
            )
        except Exception:
            pass
    except Exception:
        # Something in the loop itself blew up (db hiccup, unexpected error, etc) --
        # the round is still 'active' and the player's bet is still stuck, so refund
        # it here rather than silently leaving them with a frozen message and a gone
        # bet. refund_all_active_aviator_rounds() (startup recovery) and
        # aviator_watchdog() (periodic safety net) cover the OTHER way a round can go
        # stale -- the whole bot process dying mid-round, which kills this task
        # without ever reaching this except at all.
        logger.exception("aviator round %s ticker crashed, refunding", round_id)
        try:
            await refund_and_notify_aviator_round(round_id)
        except Exception:
            logger.exception("aviator round %s refund-after-crash also failed", round_id)


async def refund_and_notify_aviator_round(round_id: int) -> bool:
    """Shared refund step used by the ticker's own except-block, the startup recovery
    pass, and the periodic watchdog: force-refunds one round via db.refund_aviator()
    (a no-op if it's already resolved -- e.g. the player cashed out in the same
    instant) and, if it's still resolvable, best-effort edits its message so the
    player sees "прервана, ставка вернулась" instead of a rocket frozen forever mid-
    flight. Returns True if this call is what refunded it, False if there was nothing
    to refund (already resolved by something else, or the round doesn't exist)."""
    result = db.refund_aviator(round_id)
    if result is None:
        return False
    if result["chat_id"] and result["message_id"]:
        try:
            await bot.edit_message_text(
                chat_id=result["chat_id"],
                message_id=result["message_id"],
                text=(
                    f"\U0001F6E0 Игра прервана — ставка {result['bet']} гемов вернулась "
                    f"(баланс: {result['gems']})"
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardBuilder().as_markup(),
            )
        except Exception:
            pass
    return True


async def refund_all_active_aviator_rounds():
    """Startup recovery: any aviator_rounds row still 'active' when the bot process
    starts can only be leftover from a PREVIOUS process -- a live process always
    resolves its own rounds (win/loss/refund), so it can never see its own round still
    'active' at its own startup. A restart kills each round's in-process ticker task
    outright (it's a plain asyncio.create_task, nothing persists it across a
    process), which is exactly the "ракетка зависла" symptom: message frozen mid-
    flight, bet already taken, nothing left running to ever resolve it. Refund every
    one of them once, right before polling starts."""
    stale = db.get_stale_active_aviator_rounds(0)
    if not stale:
        return
    logger.info("refunding %d aviator round(s) left active from a previous run", len(stale))
    for row in stale:
        try:
            await refund_and_notify_aviator_round(row["id"])
        except Exception:
            logger.exception("could not refund leftover aviator round %s", row["id"])


async def aviator_watchdog():
    """Periodic safety net living for the bot process's lifetime: refunds any
    aviator_rounds row that's been 'active' for longer than a round could ever
    legitimately take (AVIATOR_TICKS' ~14 real ticks at 1s each, so 60s is a generous
    multiple of that) — covering any OTHER way a round's ticker task could die
    without a bot restart (an unhandled edge case, the task getting silently
    cancelled, etc), on top of the startup recovery for restarts and the ticker's own
    except-block for in-loop errors."""
    while True:
        await asyncio.sleep(20)
        try:
            for row in db.get_stale_active_aviator_rounds(60):
                await refund_and_notify_aviator_round(row["id"])
        except Exception:
            logger.exception("aviator watchdog iteration failed")


@dp.callback_query(F.data.startswith("aviator:"))
async def handle_aviator_cashout(call: CallbackQuery):
    try:
        _, round_id_s, owner_id_s, multiplier_s = call.data.split(":")
        round_id = int(round_id_s)
        owner_id = int(owner_id_s)
        multiplier = float(multiplier_s)
    except ValueError:
        await call.answer("Битая кнопка, начни заново через /go", show_alert=True)
        return
    if call.from_user.id != owner_id:
        await call.answer("Это не твоя игра -- начни свою через /go", show_alert=True)
        return
    try:
        result = db.cashout_aviator(round_id, call.from_user.id, multiplier)
    except db.AviatorError as e:
        await call.answer("Раунд уже завершён" if "завершён" in str(e) else f"Ошибка: {e}", show_alert=True)
        return

    await call.answer("\U0001F48E Забрал!")
    try:
        await call.message.edit_text(
            f"\U0001F389 Забрал на <b>{multiplier:.2f}x</b>!\n\nВыигрыш {result['payout']} гемов (баланс: {result['gems']})",
            parse_mode="HTML",
            reply_markup=InlineKeyboardBuilder().as_markup(),
        )
    except Exception:
        logger.warning("could not edit aviator result message for user %s", call.from_user.id)


# Countdown pings before the race ends, each fired exactly once (db.ref_race_countdown_sent
# guards it, same idempotency pattern as the final announcement below). Ordered soonest-
# first so the loop can just iterate and check each one every tick.
REF_RACE_COUNTDOWN_STAGES = [
    ("24h", timedelta(hours=24), "⏰ До конца реферальной гонки остались сутки! Успей пригласить друзей и попасть в топ-5 🏆"),
    ("4h", timedelta(hours=4), "🔥 До конца реферальной гонки осталось 4 часа! Финальный рывок"),
    ("1h", timedelta(hours=1), "⌛ До конца реферальной гонки остался 1 час! Последний шанс попасть в топ-5"),
    ("5m", timedelta(minutes=5), "🚨 До конца реферальной гонки осталось 5 минут!"),
]


async def ref_race_scheduler():
    """Background loop living for the lifetime of the bot process: posts a countdown
    ping to PUBLIC_CHAT at 24h/4h/1h/5m before REF_RACE_ANNOUNCE_AT (each exactly once,
    guarded by db.has_ref_race_countdown_been_sent()), then once real time passes
    REF_RACE_ANNOUNCE_AT itself, posts the final leaderboard exactly once (guarded by
    db.has_ref_race_been_announced(), same idempotency pattern as check_hundred_club/
    hundred_club) and keeps looping harmlessly forever after."""
    logger.info("referral race scheduler started")
    while True:
        try:
            now = datetime.now(ZoneInfo("Europe/Moscow"))
            if not db.has_ref_race_been_announced() and now >= REF_RACE_ANNOUNCE_AT:
                await sync_referral_chat_verification()
                rows = db.get_ref_leaderboard(5, exclude_id=int(ADMIN_ID) if ADMIN_ID else None)
                text = "🏁 Реферальная гонка завершена!\n\n" + format_ref_leaderboard(rows)
                try:
                    await bot.send_message(PUBLIC_CHAT, text)
                except Exception:
                    logger.warning("could not post ref race results to %s", PUBLIC_CHAT)
                db.mark_ref_race_announced()
            elif not db.has_ref_race_been_announced():
                for stage, remaining, headline in REF_RACE_COUNTDOWN_STAGES:
                    if db.has_ref_race_countdown_been_sent(stage):
                        continue
                    if now >= REF_RACE_ANNOUNCE_AT - remaining:
                        await sync_referral_chat_verification()
                        rows = db.get_ref_leaderboard(5, exclude_id=int(ADMIN_ID) if ADMIN_ID else None)
                        text = headline + "\n\n" + format_ref_leaderboard(rows)
                        try:
                            await bot.send_message(PUBLIC_CHAT, text)
                        except Exception:
                            logger.warning("could not post ref race %s countdown to %s", stage, PUBLIC_CHAT)
                        db.mark_ref_race_countdown_sent(stage)
        except Exception:
            logger.exception("ref race scheduler iteration failed")
        await asyncio.sleep(60)  # tight enough that the 5-minute checkpoint stays meaningful


async def referral_chat_verification_scheduler():
    """Background loop living for the lifetime of the bot process: periodically runs
    sync_referral_chat_verification() so a referral's queued gems pay out the moment
    their friend joins PUBLIC_CHAT, without anyone having to run /ref for it to happen
    (gems now require BOTH a first farm AND joining the chat -- see database.py's
    farm()/mark_chat_verified())."""
    logger.info("referral chat verification scheduler started (~every 2min)")
    while True:
        try:
            await sync_referral_chat_verification()
        except Exception:
            logger.exception("referral chat verification scheduler iteration failed")
        await asyncio.sleep(120)


async def main():
    db.init_db()
    logger.info("Peeppo bot starting (polling)...")
    # Registers our command menu with Telegram (setMyCommands) -- without this, a
    # group chat with more than one bot has NOTHING to disambiguate "/help" against,
    # so the client's autocomplete silently resolves it to whichever OTHER bot in the
    # chat has its own commands registered (e.g. "/help@SpamProtectionBot") instead of
    # ours, even though our code handles /help fine -- the message never reaches us.
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Открыть Peeppo"),
            BotCommand(command="help", description="Список команд в чате"),
            BotCommand(command="bank", description="Баланс гемов"),
            BotCommand(command="buy", description="Купить гемов за Telegram Stars"),
            BotCommand(command="go", description="Авиатор/ракетка"),
            BotCommand(command="redblack", description="Красное/чёрное"),
            BotCommand(command="rb", description="Красное/чёрное (короткая команда)"),
            BotCommand(command="ref", description="Своя реферальная ссылка"),
        ])
    except Exception:
        logger.exception("could not register bot command menu")
    await check_hundred_club()  # in case we already had 100+ users before this deploy
    await refund_all_active_aviator_rounds()  # clean up any /go left frozen by the restart that's happening right now
    asyncio.create_task(gem_drop_scheduler())
    asyncio.create_task(giveaway_scheduler())
    asyncio.create_task(ref_race_scheduler())
    asyncio.create_task(referral_chat_verification_scheduler())
    asyncio.create_task(giveaway_reminder_scheduler())
    asyncio.create_task(aviator_watchdog())
    asyncio.create_task(daily_help_broadcast_scheduler())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
