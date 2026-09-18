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
from pathlib import Path
from urllib.parse import urlsplit

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
STATIC_CARDS_DIR = Path(__file__).parent / "static" / "cards"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("peeppo.bot")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def _open_button():
    kb = InlineKeyboardBuilder()
    kb.button(text="Фармить", web_app=WebAppInfo(url=WEBAPP_URL))
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
        await notify_admin_new_user(message.from_user)
        if user_row["ref_by"]:
            referral_count = db.get_referral_count(user_row["ref_by"])
            rewarded = referral_count <= db.MAX_REWARDED_REFERRALS
            await notify_referral_reward(user_row["ref_by"], message.from_user, rewarded)

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


async def notify_admin_new_user(tg_user):
    """Best-effort ping to you (ADMIN_ID in .env) whenever someone brand new starts the bot."""
    if not ADMIN_ID:
        return
    who = f"@{tg_user.username}" if tg_user.username else (tg_user.first_name or str(tg_user.id))
    try:
        await bot.send_message(int(ADMIN_ID), f"Новый юзер {who}")
    except Exception:
        logger.warning("could not notify admin of new user %s", tg_user.id)


async def notify_referral_reward(referrer_id: int, new_tg_user, rewarded: bool):
    """Tells the referrer someone joined via their link — with the gem bonus only while
    they're still under db.MAX_REWARDED_REFERRALS invites."""
    who = f"@{new_tg_user.username}" if new_tg_user.username else (new_tg_user.first_name or "Новый игрок")
    if rewarded:
        text = f"{who} присоединился по твоей ссылке! +{db.REFERRAL_REWARD_GEMS} 💎 на баланс"
    else:
        text = f"{who} присоединился по твоей ссылке! Бонус за рефералов уже исчерпан (макс. {db.MAX_REWARDED_REFERRALS}), гемы в этот раз не начислены"
    try:
        await bot.send_message(referrer_id, text)
    except Exception:
        logger.warning("could not notify referrer %s of reward", referrer_id)


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
        f"Юзеров: <b>{stats['users']}</b>\n"
        f"Карточек в каталоге: <b>{stats['cards']}</b>\n"
        f"Всего сфармлено: <b>{stats['total_farmed']}</b>\n"
        f"Гемов в обороте: <b>{stats['gems_total']}</b> 💎\n\n"
        "Команды:\n"
        "/addgem id_или_@username количество — начислить (или списать отрицательным числом) гемы\n"
        "/givecard id_или_@username card_id — выдать карточку по её ID из каталога\n"
        "/finduser username — найти telegram_id и баланс по юзернейму",
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


@dp.pre_checkout_query()
async def handle_pre_checkout(pre_checkout_q: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)


@dp.message(F.successful_payment)
async def handle_successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload
    if not payload.startswith("gems:"):
        return
    _, uid_str, gems_str = payload.split(":")
    gems = int(gems_str)
    new_balance = db.add_gems(int(uid_str), gems)
    await message.answer(f"Зачислено {gems} 💎! Баланс: {new_balance} 💎")


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

async def notify_new_swap_offer(seller_id: int, offer_id: int, buyer_name: str, listing_name: str | None,
                                 photo_path: str, offered_names: list[str]):
    name = listing_name or "картинка"
    offered = ", ".join(f"«{n}»" for n in offered_names)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Принять", callback_data=f"swap_accept:{offer_id}")
    kb.button(text="❌ Отклонить", callback_data=f"swap_decline:{offer_id}")
    kb.adjust(2)
    try:
        await bot.send_photo(
            chat_id=seller_id,
            photo=FSInputFile(photo_path),
            caption=f"{buyer_name} предлагает обменять {offered} на твою «{name}»",
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


async def main():
    db.init_db()
    logger.info("Peeppo bot starting (polling)...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
