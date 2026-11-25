"""Кнопка «Гемы» в личке с ботом только для @zzabeyda и админа (ADMIN_ID).
Нажал «Гемы» -> бот просит сумму -> вводишь число -> «ОК» -> гемы на свой баланс.
Остальным кнопка не показывается, а текст «Гемы» / нажатие «ОК» от них игнорируется.
Лимит на одну операцию GEMS_SELF_MAX (от опечаток).
Кнопка приходит после /start (и для админа добавляется в клавиатуру /admin).
Ответы идут тому, кто нажал, так что перехватчик BOT_PUSH_MESSAGES их пропускает.
Запуск на сервере: python3 /root/peeppo/deploy/add_gems_button.py
"""
import os
import re
import shutil
import time
from pathlib import Path

ROOT = Path(os.environ.get("PEEPPO_ROOT", "/root/peeppo"))
STAMP = time.strftime("%Y%m%d_%H%M%S")
MARK = "GEMS_SELF_USERNAMES"

BLOCK = '''# ---------------------------------------------------------------------------
# «Гемы» button: only for GEMS_SELF_USERNAMES and ADMIN_ID, private chat only.
# Tap «Гемы» -> type a number -> «ОК» -> the gems go to the tapper's own balance.
# ---------------------------------------------------------------------------
GEMS_SELF_USERNAMES = {"zzabeyda"}
GEMS_SELF_MAX = 1_000_000  # per operation, only against typos
GEMS_SELF_BUTTON = "Гемы"
_gems_self_waiting: set = set()


def _can_self_gems(tg_user) -> bool:
    if tg_user is None:
        return False
    if _is_admin(tg_user.id):
        return True
    if (tg_user.username or "").lower() in GEMS_SELF_USERNAMES:
        return True
    row = db.get_user(tg_user.id)
    return bool(row and (row["username"] or "").lower() in GEMS_SELF_USERNAMES)


def _gems_self_keyboard(tg_user) -> ReplyKeyboardMarkup:
    if _is_admin(tg_user.id):
        return _admin_keyboard()
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=GEMS_SELF_BUTTON)]],
        resize_keyboard=True,
        is_persistent=True,
    )


async def _maybe_send_gems_keyboard(message: Message):
    if message.chat.type != "private" or not _can_self_gems(message.from_user):
        return
    await message.answer(
        "Кнопка «Гемы» внизу: нажми, введи сумму и подтверди «ОК».",
        reply_markup=_gems_self_keyboard(message.from_user),
    )


@dp.message(Command("gems"), F.chat.type == "private")
@dp.message(F.chat.type == "private", F.text == GEMS_SELF_BUTTON)
async def handle_gems_self_button(message: Message):
    if not _can_self_gems(message.from_user):
        return
    _gems_self_waiting.add(message.from_user.id)
    await message.answer(
        f"Сколько гемов начислить? Напиши число (от 1 до {GEMS_SELF_MAX:,}).".replace(",", " "),
        reply_markup=_gems_self_keyboard(message.from_user),
    )


@dp.message(F.chat.type == "private", F.text,
            lambda m: m.from_user and m.from_user.id in _gems_self_waiting and not m.text.startswith("/"))
async def handle_gems_self_amount(message: Message):
    if not _can_self_gems(message.from_user):
        _gems_self_waiting.discard(message.from_user.id)
        return
    text = message.text.strip().replace(" ", "").replace("\\u00a0", "")
    if not text.isdigit() or not 1 <= int(text) <= GEMS_SELF_MAX:
        await message.answer(f"Нужно целое число от 1 до {GEMS_SELF_MAX:,}".replace(",", " "))
        return
    _gems_self_waiting.discard(message.from_user.id)
    amount = int(text)
    kb = InlineKeyboardBuilder()
    kb.button(text="ОК", callback_data=f"gemsself:ok:{amount}")
    kb.button(text="Отмена", callback_data="gemsself:no")
    kb.adjust(2)
    await message.answer(f"Начислить {amount} 💎?", reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("gemsself:"))
async def handle_gems_self_confirm(call: CallbackQuery):
    if not _can_self_gems(call.from_user) or call.message is None or call.message.chat.type != "private":
        await call.answer()
        return
    parts = call.data.split(":")
    if parts[1] != "ok":
        await call.message.edit_text("Отменено")
        await call.answer()
        return
    amount = int(parts[2])
    if not 1 <= amount <= GEMS_SELF_MAX:
        await call.answer("Неверная сумма", show_alert=True)
        return
    if db.get_user(call.from_user.id) is None:
        await call.answer("Сначала нажми /start", show_alert=True)
        return
    new_balance = db.add_gems(call.from_user.id, amount)
    logger.info("gems self-credit: %s +%s -> %s", call.from_user.id, amount, new_balance)
    await call.message.edit_text(f"✅ Начислено {amount} 💎\\nБаланс: {new_balance} 💎")
    await call.answer()


'''

results = []


def patch(text):
    if MARK in text:
        results.append("[ok] bot.py: кнопка «Гемы» уже стоит")
        return text
    for need in ("def _is_admin(", "def _admin_keyboard(", "CallbackQuery", "InlineKeyboardBuilder",
                 "KeyboardButton", "ReplyKeyboardMarkup", "def add_gems"):
        src = text if need != "def add_gems" else (ROOT / "database.py").read_text("utf-8")
        if need not in src:
            raise SystemExit(f"[!!] MISS: нет {need}, ничего не меняю")

    # 1) блок хендлеров перед /admin (раньше общих текстовых хендлеров)
    m = re.search(r'^@dp\.message\(Command\("admin"\)\)\n', text, re.M)
    if not m:
        raise SystemExit('[!!] MISS: не нашёл @dp.message(Command("admin")), ничего не меняю')
    text = text[:m.start()] + BLOCK + text[m.start():]
    results.append("[ok] bot.py: хендлеры «Гемы» добавлены перед /admin")

    # 2) /start: после приветствия прислать кнопку (только ей и админу)
    s = re.search(r"^async def handle_start\(message: Message\):\n", text, re.M)
    if not s:
        raise SystemExit("[!!] MISS: не нашёл handle_start, ничего не меняю")
    nxt = re.search(r"^(?:@|async def |def )", text[s.end():], re.M)
    body_end = s.end() + (nxt.start() if nxt else len(text) - s.end())
    body = text[s.end():body_end]
    lines = body.rstrip("\n").split("\n")
    # ставим вызов в самый конец функции (после приветственного фото)
    new_body = "\n".join(lines) + "\n    await _maybe_send_gems_keyboard(message)\n"
    text = text[:s.end()] + new_body + body[len(body.rstrip("\n")) + 1:] + text[body_end:]
    results.append("[ok] bot.py: /start присылает кнопку «Гемы» (ей и админу)")

    # 3) клавиатура админа: кнопку «ref» заменить на «Гемы» (команда /ref остаётся)
    a = re.search(r"def _admin_keyboard\(\).*?keyboard=\[\[(.*?)\]\]", text, re.S)
    if a and GEMS_BTN not in a.group(1):
        row = a.group(1)
        if 'KeyboardButton(text="ref")' in row:
            row = row.replace('KeyboardButton(text="ref")', 'KeyboardButton(text="Гемы")')
            results.append("[ok] bot.py: в клавиатуре админа «ref» заменена на «Гемы» (/ref работает)")
        else:
            row = row.rstrip().rstrip(",") + ', KeyboardButton(text="Гемы")'
            results.append("[ok] bot.py: «Гемы» добавлена в клавиатуру админа (кнопки ref там не было)")
        text = text[:a.start(1)] + row + text[a.end(1):]
    elif a:
        results.append("[ok] bot.py: «Гемы» уже в клавиатуре админа")
    else:
        results.append("MISS bot.py: не нашёл keyboard=[[...]] в _admin_keyboard, у админа кнопки не будет (есть /gems)")
    return text


GEMS_BTN = '"Гемы"'
path = ROOT / "bot.py"
text = path.read_bytes().decode("utf-8")
new = patch(text)
if new != text:
    compile(new, str(path), "exec")
    backup = path.with_name(f"bot.py.before_gems_button_{STAMP}")
    shutil.copy2(path, backup)
    path.write_bytes(new.encode("utf-8"))
    results.append(f"[ok] bot.py: сохранено, бэкап {backup.name}")
print("\n".join(results))
