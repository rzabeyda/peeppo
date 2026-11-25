"""Run ON THE SERVER from /root/peeppo/deploy/klondike: moves Косынка into webapp/games/*,
plugs the gem API (klondike_game.py) into api.py. Idempotent, backs up what it touches."""
import os, shutil, time
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("PEEPPO_ROOT", "/root/peeppo")
TS = time.strftime("%Y%m%d_%H%M%S")
rd = lambda n: open(os.path.join(HERE, n), encoding="utf8").read()

# 1) static game files + server module
os.makedirs(f"{ROOT}/webapp/games", exist_ok=True)
for f in ("klondike.js", "klondike.css"):
    shutil.copyfile(os.path.join(HERE, "games", f), f"{ROOT}/webapp/games/{f}")
    os.chmod(f"{ROOT}/webapp/games/{f}", 0o644)
if os.path.exists(f"{ROOT}/klondike_game.py"):
    shutil.copyfile(f"{ROOT}/klondike_game.py", f"{ROOT}/klondike_game.py.before_{TS}")
shutil.copyfile(os.path.join(HERE, "klondike_game.py"), f"{ROOT}/klondike_game.py")

# 2) index.html: drop the old inline copy, keep the button + hooks, load the external files
ip = f"{ROOT}/webapp/index.html"
s = open(ip, encoding="utf8").read()
orig = s
for frag in ("kl.css", "kl_rules.html", "kl.js"):
    t = rd(frag)
    if frag == "kl.js": t = "\n" + t
    if t in s: s = s.replace(t, "", 1)
old_block = rd("kl_block.html")
if old_block in s: s = s.replace(old_block, '      <div id="pvp-klondike-block" style="display:none"></div>\n', 1)
assert 'id="pvp-klondike-block"' in s, "klondike block missing in index.html"
assert 'data-mode="klondike"' in s, "klondike button missing in index.html"
assert "/* ---------- Косынка (Klondike) ---------- */" not in s, "old inline css still there"
assert "Косынка (Klondike solitaire) -- free, purely client-side" not in s, "old inline js still there"
if "/games/klondike.css" not in s:
    s = s.replace("</head>", '<link rel="stylesheet" href="/games/klondike.css">\n</head>', 1)
if "/games/klondike.js" not in s:
    i = s.rfind("</body>")
    s = s[:i] + '<script src="/games/klondike.js"></script>\n' + s[i:]
if s != orig:
    shutil.copyfile(ip, f"{ip}.before_klondike_files_{TS}")
    open(ip, "w", encoding="utf8", newline="").write(s)
    print("index.html patched")

# 3) api.py: plug the router in (two lines at the end)
ap = f"{ROOT}/api.py"
a = open(ap, encoding="utf8").read()
if "klondike_game" not in a:
    shutil.copyfile(ap, f"{ap}.before_klondike_{TS}")
    a = a.rstrip("\n") + "\n\n\n# Косынка on gems -- see klondike_game.py\nimport klondike_game  # noqa: E402\napp.include_router(klondike_game.make_router(_authenticate))\n"
    open(ap, "w", encoding="utf8", newline="").write(a)
    print("api.py patched")
print("ok")
