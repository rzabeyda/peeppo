"""
One-off: sets a Russian display name for every card in the catalog, keyed by filename.
Purely cosmetic and fully reversible — rerun with an empty NAMES dict (or just don't
run it again) to leave things as they were; existing names are simply overwritten.

Usage: python3 set_card_names.py
"""

import database as db

NAMES = {
    "hudi.jpg": "Худи",
    "ono.jpg": "Онo",
    "audi.jpg": "Ауди",
    "minecraft.jpg": "Майнкрафт",
    "sigma.jpg": "Сигма",
    "ipod.jpg": "iPod",
    "dubai.jpg": "Дубай",
    "playstation.jpg": "PlayStation",
    "bloging.jpg": "Блогинг",
    "notebook.jpg": "Ноутбук",
    "burger.jpg": "Бургер",
    "altuwka.jpg": "Тачка",
    "iphone.jpg": "iPhone",
    "energy_drink.jpg": "Энергетик",
    "skibidi.jpg": "Скибиди",
    "dog.jpg": "Собака",
    "slovopacana.jpg": "Слово Пацана",
    "phone.jpg": "Телефон",
    "kfc.jpg": "KFC",
    "euro.jpg": "Евро",
    "nike_boots.jpg": "Кроссы Nike",
    "bober.jpg": "Бобёр",
    "golub.jpg": "Голубь",
    "capibara.jpg": "Капибара",
    "fortnite.jpg": "Фортнайт",
    "cs.jpg": "CS",
    "nike_suit.jpg": "Костюм Nike",
    "btc.jpg": "Биткоин",
    "samokat.jpg": "Самокат",
    "cat.jpg": "Кот",
    "youtube.jpg": "YouTube",
    "kremlin.jpg": "Кремль",
    "dota.jpg": "Dota",
    "adidas_suit.jpg": "Костюм Adidas",
    "lamba.jpg": "Ламба",
    "delivery.jpg": "Доставка",
    "xbox.jpg": "Xbox",
    "chatgpt.jpg": "ChatGPT",
    "liberty.jpg": "Статуя Свободы",
    "brawl.jpg": "Brawl Stars",
    "bmw.jpg": "BMW",
    "airpods.jpg": "AirPods",
    "dollars.jpg": "Доллары",
    "skate.jpg": "Скейт",
    "tiktok.jpg": "TikTok",
    "france.jpg": "Франция",
    "roblox.jpg": "Roblox",
    "moped.jpg": "Мопед",
    "ipad.jpg": "iPad",
    "veip.jpg": "Вейп",
}

with db.get_conn() as conn:
    updated, missing = 0, []
    for filename, name in NAMES.items():
        cur = conn.execute("UPDATE cards SET name = ? WHERE filename = ?", (name, filename))
        if cur.rowcount:
            updated += 1
        else:
            missing.append(filename)
    print(f"named {updated} card(s)")
    if missing:
        print("not found in catalog (skipped):", ", ".join(missing))
