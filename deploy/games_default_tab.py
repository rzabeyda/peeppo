"""Makes «Игры» (pvp-screen) the tab the mini-app opens on. usage: python games_default_tab.py index.html"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf8").read()
if 'switchScreen("pvp-screen"), 0)' in s:
    print("already patched"); sys.exit(0)
old = ('document.querySelector(\'.nav-btn[data-screen="profile-screen"]\').classList.add("active");\n'
       'document.getElementById("profile-screen").classList.add("active");\n'
       'loadProfile();\n')
assert s.count(old) == 1, "anchor not found"
new = ('// Default tab on open: «Игры» (pvp-screen). loadProfile() still runs for the balance/header;\n'
       '// switchScreen is deferred because the pvp state variables further down are not initialised yet.\n'
       'document.querySelectorAll(".nav-btn").forEach(b=>b.classList.remove("active"));\n'
       'document.querySelector(\'.nav-btn[data-screen="pvp-screen"]\').classList.add("active");\n'
       'document.getElementById("pvp-screen").classList.add("active");\n'
       'loadProfile();\n'
       'setTimeout(()=>switchScreen("pvp-screen"), 0);\n')
s = s.replace(old, new)
open(p, "w", encoding="utf8", newline="").write(s)
print("patched")
