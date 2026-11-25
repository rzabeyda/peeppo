"""Убирает обмен Diamond-карт на Telegram Stars прямо в серверных файлах:
кнопка «Продать Diamond за Звёзды» в вебаппе, фраза в /start и в описании бота.
Запуск на сервере из /root/peeppo: python3 deploy/remove_stars_exchange.py
"""
import json
import re
import shutil
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path("/root/peeppo")
STAMP = time.strftime("%Y%m%d_%H%M%S")

PATCHES = [
    (ROOT / "bot.py",
     '"на рынке. Обменивай Diamond карты на Telegram Stars."',
     '"на рынке."'),
    (ROOT / "webapp" / "index.html",
     '<button id="gems-choice-sell">',
     '<button id="gems-choice-sell" style="display:none">'),
]

for path, old, new in PATCHES:
    text = path.read_text(encoding="utf-8")
    if new in text and old not in text:
        print(f"[ok] {path.name}: уже исправлено")
        continue
    if text.count(old) != 1:
        raise SystemExit(f"[!!] {path.name}: не нашёл строку ровно один раз, ничего не меняю: {old}")
    shutil.copy2(path, path.with_name(f"{path.name}.before_no_stars_exchange_{STAMP}"))
    path.write_text(text.replace(old, new), encoding="utf-8")
    print(f"[ok] {path.name}: исправлено (бэкап рядом)")

# --- описание бота (BotFather description / short description) ---
token = None
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    if line.strip().startswith("BOT_TOKEN"):
        token = line.split("=", 1)[1].strip().strip('"').strip("'")
if not token:
    raise SystemExit("[!!] BOT_TOKEN не найден в .env")


def api(method, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}", data) as r:
        return json.loads(r.read())["result"]


# Предложение/строка, где речь об обмене/продаже/выводе Diamond (карт) на звёзды.
STARS_RE = re.compile(r"(stars|звёзд|звезд|⭐)", re.I)
TRADE_RE = re.compile(r"(обмен|продава|продай|вывод|выводи|меняй)", re.I)
CARD_RE = re.compile(r"(diamond|даймонд|алмаз|карт)", re.I)


def strip_exchange(text):
    out_lines = []
    for line in text.split("\n"):
        parts = re.split(r"(?<=[.!?])\s+", line)
        kept = [p for p in parts if not (STARS_RE.search(p) and TRADE_RE.search(p) and CARD_RE.search(p))]
        new_line = " ".join(kept).rstrip()
        if line.strip() and not new_line.strip():
            continue  # строка целиком была про обмен — выкидываем её
        out_lines.append(new_line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip()


for get_m, set_m, key in (("getMyDescription", "setMyDescription", "description"),
                          ("getMyShortDescription", "setMyShortDescription", "short_description")):
    old = api(get_m).get(key, "")
    new = strip_exchange(old)
    if new == old:
        print(f"[ok] {key}: упоминания обмена на звёзды нет")
        continue
    print(f"--- {key} было:\n{old}\n--- стало:\n{new}\n")
    api(set_m, **{key: new})
    print(f"[ok] {key}: обновлено")
