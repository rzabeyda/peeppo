"""Выключает флагом BOT_PUSH_MESSAGES = False (код не удаляется) всё, что бот шлёт сам:
  1) bot.py: перехватчик на уровне сессии бота. Любое новое сообщение (send_*/copy/forward)
     уходит только тому, кто сейчас нажал команду/кнопку, и в тот чат, где нажал.
     Шедулеры (напоминания о розыгрышах, реф-гонка, «Ферма», /help в чат, итоги розыгрышей),
     рассылки и пуши из API (продажа карты, подарок, алмаз) -- блокируются и пишутся в лог
     строкой "push blocked". Редактирование уже висящих постов не трогается, розыгрыши
     дальше разыгрываются как обычно. Команды админа (ADMIN_ID) работают как раньше.
     api.py сам ничего не шлёт -- он вызывает функции bot.py, так что глушится тем же флагом.
  2) bot.py: кнопки игрока в webapp остаются рабочими, это ответ на его нажатие:
     send_share_message (карта себе в личку), share_*_result («Поделиться» результатом
     игры в чат), send_pvp_invite («Позвать игрока» в PvP).
  3) daily_reminder.py (cron, «Зайди и забери сегодняшнюю награду!») -- сразу выходит.
Запуск на сервере: python3 /root/peeppo/deploy/disable_bot_push.py
"""
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(os.environ.get("PEEPPO_ROOT", "/root/peeppo"))
STAMP = time.strftime("%Y%m%d_%H%M%S")

BLOCK = '''

# ---------------------------------------------------------------------------
# BOT_PUSH_MESSAGES = False: the bot never writes to anyone on its own. A new message
# (send_*/copy/forward) goes out only to the user who triggered the update being handled
# and to the chat it came from (tasks started by a handler inherit that). Everything sent
# from schedulers, broadcasts or api.py is dropped with a "push blocked" log line. Edits of
# existing posts are not affected. Updates from ADMIN_ID may still send anywhere.
# ---------------------------------------------------------------------------
BOT_PUSH_MESSAGES = False  # off on request: no reminders/broadcasts/pushes, only replies

import contextvars as _contextvars
from aiogram.client.session.middlewares.base import BaseRequestMiddleware as _BaseRequestMiddleware
from aiogram.exceptions import TelegramAPIError as _TelegramAPIError

_push_allowed = _contextvars.ContextVar("_push_allowed", default=None)


def _allow_push_to(*chat_ids):
    """Lets the current task send to these chats even outside a handler (api.py calls)."""
    _push_allowed.set(frozenset(str(c).lower() for c in chat_ids if c is not None))


class BotPushDisabled(_TelegramAPIError):
    pass


@dp.update.outer_middleware()
async def _remember_reply_targets(handler, event, data):
    chat, user = data.get("event_chat"), data.get("event_from_user")
    allowed = set()
    if chat:
        allowed.add(str(chat.id))
        if chat.username:
            allowed.add("@" + chat.username.lower())
    if user:
        allowed.add(str(user.id))
        if ADMIN_ID and str(user.id) == str(ADMIN_ID):
            allowed.add("*")
    token = _push_allowed.set(frozenset(allowed))
    try:
        return await handler(event, data)
    finally:
        _push_allowed.reset(token)


class _NoBotPush(_BaseRequestMiddleware):
    async def __call__(self, make_request, bot, method):
        name = type(method).__name__
        if not BOT_PUSH_MESSAGES and name.startswith(("Send", "Copy", "Forward")) and name != "SendChatAction":
            allowed = _push_allowed.get()
            target = str(getattr(method, "chat_id", "")).lower()
            if allowed is None or ("*" not in allowed and target not in allowed):
                logger.info("push blocked: %s -> %s", name, target)
                raise BotPushDisabled(method=method, message="bot push messages are disabled")
        return await make_request(bot, method)


bot.session.middleware(_NoBotPush())
'''

FUNC_RE = r'^async def {name}\((\w+)[^)]*\)[^\n]*:\n((?:    """.*?"""\n)?)'
results = []


def patch_bot(text):
    if "BOT_PUSH_MESSAGES =" in text:
        results.append("[ok] bot.py: перехватчик уже стоит")
    else:
        m = re.search(r"^dp = Dispatcher\(.*\)\n", text, re.M)
        if not m:
            raise SystemExit("[!!] MISS: не нашёл строку dp = Dispatcher(...), ничего не меняю")
        head = text[:m.start()]
        for need in (r"^bot = Bot\(", r"^ADMIN_ID\s*=", r"^logger\s*="):
            if not re.search(need, head, re.M):
                raise SystemExit(f"[!!] MISS: {need} не определён до dp = Dispatcher(), ничего не меняю")
        text = text[:m.end()] + BLOCK + text[m.end():]
        results.append("[ok] bot.py: перехватчик BOT_PUSH_MESSAGES = False добавлен после dp = Dispatcher()")

    # Кнопки, которые игрок сам жмёт в webapp: «Поделиться» картой (в личку себе),
    # «Поделиться результатом» игры и «Позвать игрока» в PvP (в общий чат).
    allow = [("send_share_message", None, "самому игроку"),
             (r"share_\w+_result", "PUBLIC_CHAT", "в общий чат"),
             ("send_pvp_invite", "PUBLIC_CHAT", "в общий чат")]
    for name_re, target, label in allow:
        found = False
        for m in reversed(list(re.finditer(FUNC_RE.format(name=f"(?:{name_re})"), text, re.M | re.S))):
            found = True
            fn = re.match(r"async def (\w+)", m.group(0)).group(1)
            if text[m.end():].startswith("    _allow_push_to("):
                results.append(f"[ok] bot.py: {fn} уже разрешена")
                continue
            text = text[:m.end()] + f"    _allow_push_to({target or m.group(1)})  # the user's own button tap\n" + text[m.end():]
            results.append(f"[ok] bot.py: {fn} разрешена {label} (кнопка игрока)")
        if not found:
            results.append(f"MISS bot.py: {name_re} не найдена (эта кнопка из webapp будет молчать)")
    return text


def patch_reminder(text):
    if "BOT_PUSH_MESSAGES" in text:
        results.append("[ok] daily_reminder.py: уже выключено")
        return text
    m = re.search(r"^async def main\(\):\n", text, re.M)
    if not m:
        results.append("MISS daily_reminder.py: не нашёл async def main(), не тронул")
        return text
    text = (text[:m.start()]
            + "BOT_PUSH_MESSAGES = False  # off on request: no daily bonus reminders\n\n\n"
            + text[m.start():m.end()]
            + "    if not BOT_PUSH_MESSAGES:\n"
            + '        logger.info("Daily reminders are disabled (BOT_PUSH_MESSAGES = False).")\n'
            + "        return\n"
            + text[m.end():])
    results.append("[ok] daily_reminder.py: выключено, по cron просто выходит")
    return text


def apply(name, fn):
    path = ROOT / name
    if not path.exists():
        results.append(f"MISS {name}: файла нет на сервере")
        return None
    text = path.read_bytes().decode("utf-8")
    new = fn(text)
    if new != text:
        compile(new, str(path), "exec")  # не пишем файл, если что-то сломалось
        backup = path.with_name(f"{name}.before_no_push_{STAMP}")
        shutil.copy2(path, backup)
        path.write_bytes(new.encode("utf-8"))
        results.append(f"[ok] {name}: сохранено, бэкап {backup.name}")
    return new


bot_text = apply("bot.py", patch_bot)
apply("daily_reminder.py", patch_reminder)
print("\n".join(results))

# --- для контроля ---
SEND_RE = re.compile(r"\.(?:send_(?:message|photo|animation|video|document|sticker|dice|media_group|audio|voice|poll)"
                     r"|copy_message|forward_message)\s*\(|sendMessage")
if bot_text:
    print("\n--- send_* в bot.py вне хендлеров (все теперь глушатся перехватчиком, проверь глазами):")
    current, deco, is_handler, hits = None, [], False, {}
    for i, line in enumerate(bot_text.splitlines(), 1):
        if line.startswith("@"):
            deco.append(line)
            continue
        m = re.match(r"(?:async )?def (\w+)", line)
        if m:
            current = m.group(1)
            is_handler = any(d.startswith(("@dp.", "@router.")) for d in deco)
            deco = []
            continue
        if SEND_RE.search(line) and not is_handler:
            hits.setdefault(current, []).append(str(i))
    for fn, lines in hits.items():
        print(f"  {fn}: строки {', '.join(lines)}")

print("\n--- другие .py в /root/peeppo, которые сами шлют сообщения (не трогал):")
for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in ("venv", ".venv", "backup", "_trash", "__pycache__", "deploy", ".git")]
    for fname in sorted(filenames):
        p = Path(dirpath) / fname
        if not fname.endswith(".py") or p == ROOT / "bot.py":
            continue
        t = p.read_text("utf-8", errors="replace")
        n = len(SEND_RE.findall(t))
        if n:
            print(f"  {p.relative_to(ROOT)}: {n} мест" + ("  (уже выключен флагом)" if "BOT_PUSH_MESSAGES" in t else ""))

print("\n--- cron:")
print(subprocess.run("crontab -l 2>/dev/null; ls /etc/cron.d 2>/dev/null", shell=True,
                     capture_output=True, text=True).stdout.strip() or "  (пусто)")
print("--- конец")
