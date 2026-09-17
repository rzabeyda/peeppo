"""
Peeppo — aiogram bot.

Responsibilities:
  - /start (with optional referral deep-link payload "ref<telegram_id>") -> register user, show Open button
  - send_share_message() -> called by api.py when a user taps "Поделиться" in the webapp;
    sends the card photo + the user's own referral link back into their own chat so they can
    just hit Telegram's native Forward/Share-to-story on it.

Run as its own long-polling process (peeppo_bot.service), separate from api.py (peeppo_api.service),
same pattern as the other bots on this server.
"""

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

import database as db

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://peeppo.memstroy.app")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "Peeppobot")  # no leading @

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("peeppo.bot")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def _parse_ref_payload(text: str) -> int | None:
    # /start ref123456789
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    payload = parts[1].strip()
    if payload.startswith("ref"):
        try:
            return int(payload[3:])
        except ValueError:
            return None
    return None


@dp.message(CommandStart())
async def handle_start(message: Message):
    ref_by = _parse_ref_payload(message.text or "")
    db.get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        ref_by=ref_by,
    )

    kb = InlineKeyboardBuilder()
    kb.button(text="Открыть 🎮", web_app=WebAppInfo(url=WEBAPP_URL))

    await message.answer(
        "Добро пожаловать в <b>Peeppo</b>!\n\n"
        "Жми Фарм — собирай картинки, показывай друзьям, меняйся и продавай на рынке.",
        reply_markup=kb.as_markup(),
        parse_mode="HTML",
    )


async def send_share_message(user_id: int, photo_path: str, card_name: str | None):
    """Called from api.py (imports this module directly, same BOT_TOKEN) after /api/share."""
    from aiogram.types import FSInputFile

    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref{user_id}"
    caption = (
        f"Смотри что мне выпало{f' — {card_name}' if card_name else ''}! 🎁\n\n"
        f"Залетай в игру и фарми свои картинки → {ref_link}"
    )
    await bot.send_photo(chat_id=user_id, photo=FSInputFile(photo_path), caption=caption)


async def main():
    db.init_db()
    logger.info("Peeppo bot starting (polling)...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
