"""Ежедневно +1000 гемов каждому игроку (тихо, без сообщений).

Запускается cron'ом каждый час. Начисляет один раз за московские сутки:
дата последнего начисления хранится в таблице daily_gem_grants, проверка и
UPDATE идут в одной транзакции, поэтому повторный запуск/рестарт не начислит дважды.
Боты (telegram_id <= 0: PvP-«Бот» и тест-бот) не получают.
"""
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "peeppo.db"
AMOUNT = 1000
MSK = timezone(timedelta(hours=3))


def main() -> int:
    today = datetime.now(MSK).strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS daily_gem_grants ("
            "grant_date TEXT PRIMARY KEY, amount INTEGER NOT NULL, "
            "users_count INTEGER NOT NULL, granted_at TEXT NOT NULL)"
        )
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute(
            "SELECT 1 FROM daily_gem_grants WHERE grant_date = ?", (today,)
        ).fetchone():
            conn.execute("ROLLBACK")
            return 0
        cur = conn.execute(
            "UPDATE users SET gems = gems + ?, gems_earned = gems_earned + ? "
            "WHERE telegram_id > 0",
            (AMOUNT, AMOUNT),
        )
        conn.execute(
            "INSERT INTO daily_gem_grants (grant_date, amount, users_count, granted_at) "
            "VALUES (?, ?, ?, ?)",
            (today, AMOUNT, cur.rowcount, datetime.now(timezone.utc).isoformat()),
        )
        conn.execute("COMMIT")
        print(f"{datetime.now(MSK):%Y-%m-%d %H:%M} MSK: +{AMOUNT} gems to {cur.rowcount} users")
        return 0
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
