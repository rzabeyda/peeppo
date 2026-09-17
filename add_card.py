"""
Peeppo — one-off helper to register images in the card catalog.

Usage (from the server, after scp-ing images into static/cards/):
    python3 add_card.py photo1.jpg "Золотой кот"
    python3 add_card.py photo2.jpg              # name is optional

Or bulk-register every file already sitting in static/cards/ that isn't in the DB yet:
    python3 add_card.py --scan
"""

import sys
from pathlib import Path

import database as db

CARDS_DIR = Path(__file__).parent / "static" / "cards"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def scan():
    db.init_db()
    with db.get_conn() as conn:
        known = {row["filename"] for row in conn.execute("SELECT filename FROM cards")}
    added = 0
    for f in sorted(CARDS_DIR.iterdir()):
        if not f.is_file() or f.suffix.lower() not in IMAGE_EXTS:
            continue  # skips .gitkeep and any other non-image junk
        if f.name not in known:
            db.add_card_to_catalog(f.name)
            print(f"added: {f.name}")
            added += 1
    print(f"done — {added} new card(s) registered")


if __name__ == "__main__":
    db.init_db()
    if "--scan" in sys.argv:
        scan()
    elif len(sys.argv) >= 2:
        filename = sys.argv[1]
        name = sys.argv[2] if len(sys.argv) >= 3 else None
        if not (CARDS_DIR / filename).exists():
            print(f"warning: {filename} not found in {CARDS_DIR} yet (adding to catalog anyway)")
        card_id = db.add_card_to_catalog(filename, name)
        print(f"added card #{card_id}: {filename} ({name or 'no name'})")
    else:
        print(__doc__)
