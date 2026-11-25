#!/bin/bash
# Ставит ежедневное +1000 гемов: cron каждый час, начисление раз в московские сутки.
set -e
cd /root/peeppo
TS=$(date +%Y%m%d_%H%M%S)
crontab -l > /root/peeppo/crontab.before_daily_gems_$TS
python3 -m py_compile daily_gems.py
# Сегодня (25.11) помечаем как уже сделанное: первое начисление в ближайшую полночь МСК.
sqlite3 peeppo.db "CREATE TABLE IF NOT EXISTS daily_gem_grants (grant_date TEXT PRIMARY KEY, amount INTEGER NOT NULL, users_count INTEGER NOT NULL, granted_at TEXT NOT NULL); INSERT OR IGNORE INTO daily_gem_grants VALUES (strftime('%Y-%m-%d','now','+3 hours'),0,0,'seed: start from next MSK midnight');"
if crontab -l | grep -q daily_gems.py; then
  echo "cron уже был"
else
  (crontab -l; echo "1 * * * * cd /root/peeppo && python3 daily_gems.py >> /root/peeppo/daily_gems.log 2>&1") | crontab -
fi
if crontab -l | grep -q daily_gems.py; then echo "CRON OK"; else echo "CRON FAIL"; exit 1; fi
S1=$(sqlite3 peeppo.db "select sum(gems) from users")
python3 daily_gems.py
S2=$(sqlite3 peeppo.db "select sum(gems) from users")
if [ "$S1" = "$S2" ]; then echo "TEST OK: сегодня повторно не начислило"; else echo "TEST FAIL: $S1 -> $S2"; fi
sqlite3 peeppo.db "select * from daily_gem_grants"
