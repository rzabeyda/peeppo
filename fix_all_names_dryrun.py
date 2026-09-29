import sqlite3

WEAPON_ABBR = {
    "awp","ak","ak47","ar","m4a4","m4a1","usp","aug","sg553","sg","famas",
    "galil","galilar","scar20","g3sg1","ssg08","mac10","mp9","mp7","mp5",
    "ump45","p90","p250","cz75","r8","fiveseven","tec9","nova","xm1014",
    "mag7","sawedoff","m249","negev","deagle","zeus","hkp2000","p2000","sp",
}

def fix_name(raw):
    if not raw:
        return raw
    words = raw.split(" ")
    out = []
    for w in words:
        if not w:
            out.append(w)
            continue
        lw = w.lower()
        if lw in WEAPON_ABBR:
            out.append(lw.upper())
        else:
            out.append(w[0].upper() + w[1:])
    return " ".join(out)

conn = sqlite3.connect("/root/peeppo/peeppo.db")
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT id, filename, name, rarity FROM cards WHERE is_active = 1 ORDER BY id").fetchall()

changes = []
for r in rows:
    old = r["name"] or ""
    new = fix_name(old)
    if new != old:
        changes.append((r["id"], r["filename"], old, new))

print(f"Total active cards: {len(rows)}")
print(f"Would change: {len(changes)}")
print()
for cid, fn, old, new in changes:
    print(f"[{cid}] {fn}: {old!r} -> {new!r}")
