"""Блок «Доход» в профиле: показывать ежедневный бонус + бесплатные 1000 гемов."""
import shutil
import sys
from datetime import datetime

PATH = "/root/peeppo/webapp/index.html"
REPLACEMENTS = [
    (
        'document.getElementById("stat-income").textContent = auth.daily_bonus_amount || 25;',
        'document.getElementById("stat-income").textContent = (auth.daily_bonus_amount || 25) + 1000; // +1000 free daily gems',
    ),
    (
        'const amount = parseInt(document.getElementById("stat-income").textContent, 10) || 25;',
        'const amount = (parseInt(document.getElementById("stat-income").textContent, 10) - 1000) || 25; // minus free daily 1000',
    ),
]

src = open(PATH, encoding="utf-8").read()
for old, new in REPLACEMENTS:
    if src.count(old) != 1:
        print(f"FAIL: найдено {src.count(old)} раз: {old[:70]}")
        sys.exit(1)
backup = f"{PATH}.before_income1000_{datetime.now():%Y%m%d_%H%M%S}"
shutil.copy2(PATH, backup)
for old, new in REPLACEMENTS:
    src = src.replace(old, new)
open(PATH, "w", encoding="utf-8").write(src)
check = open(PATH, encoding="utf-8").read()
if all(new in check for _, new in REPLACEMENTS):
    print(f"OK, бэкап: {backup}")
else:
    shutil.copy2(backup, PATH)
    print("FAIL: проверка не прошла, файл восстановлен")
    sys.exit(1)
