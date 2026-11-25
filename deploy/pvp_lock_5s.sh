#!/bin/bash
# PvP-отсчёт 5с (+ добивает «Админ» и фарм 2с, если прошлый sed не сработал). С бэкапами.
set -e
cd /root/peeppo
TS=$(date +%Y%m%d_%H%M%S)
for f in bot.py database.py webapp/index.html pvp_ai.py api.py; do cp "$f" "$f.before_pvp5_$TS"; done
python3 - <<'PY'
import re
def sub(path, pat, rep, flags=re.M):
    s = open(path, encoding="utf-8", newline="").read()
    n = re.subn(pat, rep, s, flags=flags)
    open(path, "w", encoding="utf-8", newline="").write(n[0])
    print(f"{path}: {pat!r} -> {n[1]} замен")
sub("database.py", r"^PVP_LOCK_SECONDS\s*=\s*\d+", "PVP_LOCK_SECONDS = 5")
sub("database.py", r'^(\s+)return "Бот"(\r?)$', r'\1return "Админ"\2')
sub("bot.py", r'return "Бот"', 'return "Админ"')
sub("bot.py", r'if username == "Бот":', 'if username == "Админ":')
sub("webapp/index.html", r'=== "rzabeyda"\) \? "Бот" : u;', '=== "rzabeyda") ? "Админ" : u;')
sub("webapp/index.html", r'maskedName\((\w)\.username\) === "Бот" \? "Бот"', r'maskedName(\1.username) === "Админ" ? "Админ"')
sub("webapp/index.html", r"^(\s*)const duration = 3000;", r"\1const duration = 2000;")
PY
echo "--- проверка ---"
grep -n "PVP_LOCK_SECONDS\|timedelta(seconds=" database.py pvp_ai.py api.py | head -20
grep -n '"Бот"\|"Админ"' bot.py database.py webapp/index.html
grep -n "const duration = " webapp/index.html
python3 -m py_compile bot.py database.py api.py pvp_ai.py
systemctl restart peeppo_bot peeppo_api
sleep 3
systemctl is-active peeppo_bot peeppo_api
echo "OK, backups: *.before_pvp5_$TS"
