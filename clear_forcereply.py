"""
One-off: the old /buy ForceReply prompt is stuck client-side in some chats (Telegram
clients keep re-showing "reply to this message" every time the chat is reopened, until
a NEW message arrives with reply_markup=ReplyKeyboardRemove, or the user manually
dismisses it). Deploying the code fix stops FUTURE ForceReply prompts but does nothing
for ones already stuck -- this actively clears them by sending exactly that kind of
message to every chat that could plausibly have the stuck prompt (the admin's own DM
with the bot, plus every chat_id ever seen in aviator_rounds, which covers the group
chat used for /go, /redblack, /buy).

Run once from /root/peeppo on the server: python3 clear_forcereply.py
"""
import asyncio
import os
import sqlite3

from dotenv import load_dotenv
from aiogram import Bot
from aiogram.types import ReplyKeyboardRemove

load_dotenv()
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = os.environ.get("ADMIN_ID")


async def main():
    bot = Bot(token=BOT_TOKEN)
    targets = set()
    if ADMIN_ID:
        try:
            targets.add(int(ADMIN_ID))
        except ValueError:
            pass

    conn = sqlite3.connect("peeppo.db")
    try:
        rows = conn.execute(
            "SELECT DISTINCT chat_id FROM aviator_rounds WHERE chat_id IS NOT NULL"
        ).fetchall()
        for (chat_id,) in rows:
            targets.add(chat_id)
    except Exception as e:
        print("aviator_rounds query skipped:", e)
    conn.close()

    if not targets:
        print("No target chats found -- nothing to clear.")
        return

    for chat_id in targets:
        try:
            await bot.send_message(
                chat_id,
                "✅ Обновили бота.",
                reply_markup=ReplyKeyboardRemove(),
            )
            print("cleared", chat_id)
        except Exception as e:
            print("failed", chat_id, e)

    await bot.session.close()


asyncio.run(main())
