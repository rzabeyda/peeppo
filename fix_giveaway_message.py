"""
One-off: the currently-live /cardsgiveaway post in chat still shows the old,
verbose announcement text (created before the wording fix was deployed) --
new joins re-edit it with fresh code, but nothing re-edits it just because the
code changed. This edits the live post in place, right now, with the current
(short) wording and an accurate participant count, instead of waiting for the
next join or for the giveaway to draw.

Run once from /root/peeppo on the server: python3 fix_giveaway_message.py
"""
import asyncio
import os
import sqlite3
from datetime import datetime

from dotenv import load_dotenv
from aiogram import Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder

load_dotenv()
BOT_TOKEN = os.environ["BOT_TOKEN"]


def card_batch_giveaway_text(total_cards: int, hours: float, entry_count: int) -> str:
    hours_label = f"{int(hours)}ч" if float(hours).is_integer() else f"{hours:g}ч"
    return (
        f"\U0001F389 Розыгрыш {total_cards} карт!\n\n"
        f"Разыгрываются между всеми участниками\n"
        f"Жми «Участвовать» -- итоги через {hours_label}\n"
        f"Участники: {entry_count}"
    )


async def main():
    conn = sqlite3.connect("peeppo.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM card_batch_giveaways WHERE drawn_at IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        print("No active card batch giveaway found -- nothing to fix.")
        return
    if not row["message_id"] or not row["chat_id"]:
        print("Active giveaway has no message_id/chat_id yet -- nothing to edit.")
        return

    count = conn.execute(
        "SELECT COUNT(*) FROM card_batch_giveaway_entries WHERE batch_id = ?", (row["id"],)
    ).fetchone()[0]
    created = datetime.fromisoformat(row["created_at"])
    draw_at = datetime.fromisoformat(row["draw_at"])
    hours = (draw_at - created).total_seconds() / 3600

    text = card_batch_giveaway_text(row["total_cards"], hours, count)
    kb = InlineKeyboardBuilder()
    kb.button(text="Участвовать", callback_data=f"cardsgiveaway_join:{row['id']}")

    bot = Bot(token=BOT_TOKEN)
    try:
        await bot.edit_message_text(
            chat_id=row["chat_id"], message_id=row["message_id"], text=text,
            reply_markup=kb.as_markup(),
        )
        print(f"Fixed message for batch #{row['id']} (chat {row['chat_id']}, msg {row['message_id']})")
    except Exception as e:
        print("Edit failed:", e)
    await bot.session.close()


asyncio.run(main())
