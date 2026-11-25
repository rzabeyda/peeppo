#!/bin/bash
# Админ вместо «Бот» для rzabeyda, PvP-отсчёт 10с, анимация фарма 2с. С бэкапами.
set -e
cd /root/peeppo
TS=$(date +%Y%m%d_%H%M%S)
python3 -c "import sqlite3;s=sqlite3.connect('peeppo.db');d=sqlite3.connect('peeppo.db.before_admin_name_$TS');s.backup(d);d.close()"
for f in bot.py database.py webapp/index.html; do cp "$f" "$f.before_admin_name_$TS"; done

sed -i 's/return "Бот"/return "Админ"/; s/if username == "Бот":/if username == "Админ":/' bot.py
sed -i 's/^        return "Бот"$/        return "Админ"/; s/^PVP_LOCK_SECONDS = 20$/PVP_LOCK_SECONDS = 10/' database.py
sed -i 's/=== "rzabeyda") ? "Бот" : u;/=== "rzabeyda") ? "Админ" : u;/; s/(maskedName(l.username) === "Бот" ? "Бот"/(maskedName(l.username) === "Админ" ? "Админ"/; s/(maskedName(n.username) === "Бот" ? "Бот"/(maskedName(n.username) === "Админ" ? "Админ"/; s/^  const duration = 3000;$/  const duration = 2000;/' webapp/index.html

grep -n '"Бот"\|"Админ"\|PVP_LOCK_SECONDS =\|const duration = ' bot.py database.py webapp/index.html
python3 -m py_compile bot.py database.py
systemctl restart peeppo_bot peeppo_api
sleep 3
systemctl is-active peeppo_bot peeppo_api
echo "OK, backups: *.before_admin_name_$TS"
