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

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
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
BOT_USERNAME = os.environ.get("BOT_USERNAME", "Peeppobot")  # no leading @
STATIC_CARDS_DIR = Path(__file__).parent / "static" / "cards"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("peeppo.bot")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def _open_button():
    kb = InlineKeyboardBuilder()
    kb.button(text="Открыть 🎮", web_app=WebAppInfo(url=WEBAPP_URL))
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

    db.get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        ref_by=ref_by,
    )

    if payload.startswith("claim"):
        await _handle_claim(message, payload)
        return

    await message.answer(
        "Добро пожаловать в <b>Peeppo</b>!\n\n"
        "Жми Фарм — собирай картинки, показывай друзьям, меняйся и продавай на рынке.",
        reply_markup=_open_button(),
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
    """Called from api.py (imports this module directly, same BOT_TOKEN) after /api/share."""
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref{user_id}"
    caption = f"«{card_name}» 🎁 Залетай в Peeppo → {ref_link}" if card_name else f"Мой дроп 🎁 Залетай в Peeppo → {ref_link}"
    await bot.send_photo(chat_id=user_id, photo=FSInputFile(photo_path), caption=caption)


async def main():
    db.init_db()
    logger.info("Peeppo bot starting (polling)...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
