"""Убирает из профиля два промо-блока сверху: «Ферма» (farm-promo, @farm6bot) и «Чат» (chat-promo).
Удаляет разметку farm-row и их обработчики клика. Иконку вкладки «Фарм» не трогает.
Запуск на сервере: python3 /root/peeppo/deploy/remove_profile_promos.py
"""
import os
import re
import shutil
import time
from pathlib import Path

ROOT = Path(os.environ.get("PEEPPO_ROOT", "/root/peeppo"))
F = ROOT / "webapp" / "index.html"
STAMP = time.strftime("%Y%m%d_%H%M%S")

src = F.read_text(encoding="utf-8")
out = src

steps = [
    ("html farm-row", re.compile(r'[ \t]*<div class="farm-row">.*?(?=[ \t]*<div class="profile-card")', re.S)),
    ("js farm-promo click", re.compile(r'document\.getElementById\("farm-promo"\)\.addEventListener\("click",.*?\n\}\);\n', re.S)),
    ("js chat-promo click", re.compile(r'document\.getElementById\("chat-promo"\)\.addEventListener\("click",.*?\n\}\);\n', re.S)),
]
for name, rx in steps:
    out, n = rx.subn("", out, count=1)
    print(("[ok] " if n else "MISS ") + name)

left = [k for k in ('id="farm-promo"', 'id="chat-promo"', 'getElementById("farm-promo")', 'getElementById("chat-promo")') if k in out]
if left:
    print("MISS still present:", left)

if out != src:
    bak = F.with_name(F.name + ".before_remove_promos_" + STAMP)
    shutil.copy2(F, bak)
    F.write_text(out, encoding="utf-8")
    print("[ok] saved, backup:", bak)
else:
    print("nothing changed")
