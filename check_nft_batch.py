"""One-off diagnostic: for each file in the new NFT arcade batch, reports whether the
image exists on disk in static/cards/ and whether/how it's registered in the catalog."""

from pathlib import Path
import database as db

CARDS_DIR = Path(__file__).parent / "static" / "cards"
FILENAMES = [
    "tekken.jpg", "mortal_kombat.jpg", "street_fighter.jpg", "cadillacs_dino.jpg",
    "metal_slug.jpg", "iphone_diamond.jpg", "iphone_platinum.jpg", "biliard.jpg",
]

with db.get_conn() as conn:
    for filename in FILENAMES:
        on_disk = (CARDS_DIR / filename).exists()
        row = conn.execute(
            "SELECT id, name, rarity, is_active FROM cards WHERE filename = ?", (filename,)
        ).fetchone()
        if row is None:
            db_status = "NOT in catalog"
        else:
            db_status = f"id={row['id']} name={row['name']!r} rarity={row['rarity']} is_active={row['is_active']}"
        print(f"{filename}: on_disk={on_disk} | {db_status}")
