"""Подключает «Бота» -- соперника в PvP (pvp_ai.py) к процессу peeppo_bot и печатает,
сколько карт ставят живые игроки (по ним Бот выбирает свою ставку).
Перед запуском положи pvp_ai.py в /root/peeppo/.
Запуск на сервере: cd /root/peeppo && python3 deploy/add_pvp_bot.py && systemctl restart peeppo_bot
"""
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("PEEPPO_ROOT", "/root/peeppo"))
STAMP = time.strftime("%Y%m%d_%H%M%S")
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

if not (ROOT / "pvp_ai.py").exists():
    raise SystemExit("[!!] нет /root/peeppo/pvp_ai.py -- сначала скопируй его")

path = ROOT / "bot.py"
text = path.read_bytes().decode("utf-8")
if "pvp_ai_loop" in text:
    print("[ok] bot.py: Бот уже подключён")
else:
    m = re.search(r"^(async def main\(\):\n(?:.*\n)*?)(    await dp\.start_polling\(bot\)\n)", text, re.M)
    if not m:
        raise SystemExit("[!!] MISS: не нашёл await dp.start_polling(bot) в main(), ничего не меняю")
    add = ("    from pvp_ai import pvp_ai_loop  # «Бот» -- соперник в PvP\n"
           "    _background_tasks.add(asyncio.create_task(pvp_ai_loop()))\n")
    new = text[:m.start(2)] + add + text[m.start(2):]
    compile(new, str(path), "exec")
    backup = path.with_name(f"bot.py.before_pvp_ai_{STAMP}")
    shutil.copy2(path, backup)
    path.write_bytes(new.encode("utf-8"))
    print(f"[ok] bot.py: Бот подключён в main(), бэкап {backup.name}")

import pvp_ai  # noqa: E402

s = pvp_ai.player_stake_stats()
print("\n--- сколько карт ставят игроки за раунд (последние %d раундов):" % pvp_ai.STATS_ROUNDS)
if not s:
    print("  истории нет -- Бот будет ставить %d..%d" % (pvp_ai.FALLBACK_MIN, pvp_ai.FALLBACK_MAX))
else:
    print(f"  ставок: {s['stakes']}")
    print(f"  карт:  мин {s['min']}, макс {s['max']}, среднее {s['avg']}, медиана {s['median']}")
    print(f"  вес:   мин {s['w_min']}, макс {s['w_max']}, среднее {s['w_avg']}, медиана {s['w_median']}")
    print("  Бот: %d..%d карт, чаще всего около %d" % (min(s['min'], pvp_ai.STAKE_CAP), min(s['max'], pvp_ai.STAKE_CAP), s['median']))
print("--- конец")
