"""
Peeppo — daily bonus reminder.

Run once a day via cron on the server (separate from bot.py's own long-polling
process — this script just sends a batch of messages and exits, it does not
start polling, so it's safe to run alongside the running bot service).

For every user who has NOT yet claimed today's (UTC) daily 25-gem bonus, sends
a short reminder message. Users who blocked the bot or deleted their account
are skipped (Telegram raises for those sends — caught per-user so one bad
chat_id doesn't stop the rest of the batch).
"""

import asyncio
import logging
import os

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from dotenv import load_dotenv

import database as db

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("daily_reminder")

REMINDER_TEXT = f"Зайди и забери сегодняшнюю награду! 💎 +{db.DAILY_BONUS_GEMS} гемов ждут тебя"


async def main():
    db.init_db()
    user_ids = db.get_users_missing_daily_bonus()
    if not user_ids:
        logger.info("Everyone already claimed today's bonus — nothing to send.")
        return

    bot = Bot(token=BOT_TOKEN)
    sent, failed = 0, 0
    try:
        for telegram_id in user_ids:
            try:
                await bot.send_message(telegram_id, REMINDER_TEXT)
                sent += 1
            except TelegramAPIError as e:
                failed += 1
                logger.warning("Couldn't message %s: %s", telegram_id, e)
    finally:
        await bot.session.close()

    logger.info("Daily bonus reminders: sent=%d failed=%d total=%d", sent, failed, len(user_ids))


if __name__ == "__main__":
    asyncio.run(main())
