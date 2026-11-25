"""Выключает флагами (код не удаляется) в серверном bot.py:
  1) все личные уведомления админу (функции notify_admin_*: новый юзер, покупки за Stars,
     заявки на вывод) -- флаг ADMIN_DM_NOTIFICATIONS = False;
  2) вообще все дропы гемов: авто-шедулеры (*gem_drop*/*airdrop*), ручной /gem
     (_post_gem_drop) и кнопка «Забрать» на уже висящих дропах (handle_gem_claim) --
     флаг GEM_DROPS = False.
Остальной файл не трогается (CHAT_MODERATION, gift20bot и т.д. остаются как есть).
Запуск на сервере: python3 /root/peeppo/deploy/disable_admin_dm_airdrops.py
"""
import re
import shutil
import time
from pathlib import Path

PATH = Path("/root/peeppo/bot.py")
STAMP = time.strftime("%Y%m%d_%H%M%S")

text = PATH.read_bytes().decode("utf-8")
orig = text

# --- флаги рядом с ADMIN_ID ---
FLAGS = (
    "ADMIN_DM_NOTIFICATIONS = False  # off on request: bot sends no DMs to the admin (notify_admin_*)\n"
    "GEM_DROPS = False  # off on request: no gem drops at all -- auto, manual /gem, or claiming old ones\n"
)
if "ADMIN_DM_NOTIFICATIONS =" not in text:
    m = re.search(r'^ADMIN_ID = os\.environ\.get\("ADMIN_ID"\).*\n', text, re.M)
    if not m:
        raise SystemExit("[!!] не нашёл строку ADMIN_ID = os.environ.get(...), ничего не меняю")
    text = text[:m.end()] + FLAGS + text[m.end():]

# Вставляет guard первой строкой тела функции (после докстринга, если он есть).
FUNC_RE = r'^async def ({name})\([^)]*\)[^\n]*:\n((?:    """.*?"""\n)?)'


def add_guard(text, name_re, flag, body):
    patched = []

    def repl(m):
        if text[m.end():].startswith(f"    if not {flag}:"):
            print(f"[ok] {m.group(1)}: уже выключено")
            return m.group(0)
        patched.append(m.group(1))
        return m.group(0) + f"    if not {flag}:\n" + "".join(f"        {b}\n" for b in body)

    new = re.sub(FUNC_RE.format(name=name_re), repl, text, flags=re.M | re.S)
    for n in patched:
        print(f"[ok] {n}: выключено флагом {flag}")
    return new


text = add_guard(text, r"notify_admin_\w+", "ADMIN_DM_NOTIFICATIONS",
                 ["return  # admin DMs disabled"])
text = add_guard(text, r"\w*(?:gem_drop|airdrop)\w*_scheduler", "GEM_DROPS",
                 ["return  # gem drops disabled"])
text = add_guard(text, r"_post_gem_drop", "GEM_DROPS",
                 ["return False  # gem drops disabled"])
text = add_guard(text, r"handle_gem_claim", "GEM_DROPS",
                 ['await call.answer("Дропы выключены", show_alert=True)', "return"])
for fn in ("_post_gem_drop", "handle_gem_claim"):
    if f"async def {fn}(" in text and not re.search(FUNC_RE.format(name=fn) + "    if not GEM_DROPS:", text, re.M | re.S):
        raise SystemExit(f"[!!] не смог выключить {fn}, ничего не меняю")

if text == orig:
    print("[ok] bot.py: всё уже выключено, ничего не менял")
else:
    compile(text, str(PATH), "exec")  # не пишем файл, если что-то сломалось
    shutil.copy2(PATH, PATH.with_name(f"bot.py.before_no_admin_dm_airdrops_{STAMP}"))
    PATH.write_bytes(text.encode("utf-8"))
    print(f"[ok] bot.py: сохранено, бэкап bot.py.before_no_admin_dm_airdrops_{STAMP}")

# Для контроля: прямые отправки админу вне функций notify_admin_* (если такие есть на сервере).
print("\n--- остальные места, где бот пишет на ADMIN_ID (проверь глазами):")
current = None
for i, line in enumerate(text.splitlines(), 1):
    m = re.match(r"(?:async )?def (\w+)", line)
    if m:
        current = m.group(1)
    if "ADMIN_ID" in line and "send_" in line and not (current or "").startswith("notify_admin_"):
        print(f"  {i} [{current}]: {line.strip()}")
print("--- конец")
