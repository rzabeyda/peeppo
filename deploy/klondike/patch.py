import sys, os
src, dst = sys.argv[1], sys.argv[2]
d = os.path.dirname(os.path.abspath(__file__))
rd = lambda n: open(os.path.join(d, n), encoding="utf8").read()
s = open(src, encoding="utf8").read()
assert "pvp-klondike-block" not in s, "already patched"

def ins(anchor, text, before=True):
    global s
    assert s.count(anchor) == 1, anchor
    s = s.replace(anchor, text + anchor if before else anchor + text)

ICON = ("data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E"
        "%3Crect x='2' y='4' width='12' height='17' rx='2' fill='%23ddd' transform='rotate(-12 8 12)'/%3E"
        "%3Crect x='8' y='2' width='13' height='18' rx='2' fill='%23fff' stroke='%23999' stroke-width='.6'/%3E"
        "%3Cpath d='M14.5 6c-2.2 2.4-4 3.6-4 5.6 0 1.4 1.8 2.3 3.2 1.2l-.7 2.7h3l-.7-2.7c1.4 1.1 3.2.2 3.2-1.2 0-2-1.8-3.2-4-5.6z' fill='%23d1123f'/%3E"
        "%3C/svg%3E")

ins("  .rb-card{\n", rd("kl.css"))
rb_btn = '<button class="mode-switch-btn" data-mode="redblack"><img class="mode-switch-icon" src="/static/icons/red.png" alt="R&amp;B"><span class="mode-switch-label">Red&amp;Black</span></button>\n'
ins(rb_btn, f'        <button class="mode-switch-btn" data-mode="klondike"><img class="mode-switch-icon" src="{ICON}" alt="Косынка"><span class="mode-switch-label">Косынка</span></button>\n', before=False)
ins('    </section>\n\n    <section id="market-screen"', rd("kl_block.html"))
ins('  <div id="dice-rules-overlay">\n', rd("kl_rules.html"))
ins('  document.getElementById("pvp-dice-block").style.display = pvpMode === "dice" ? "" : "none";\n',
    '  document.getElementById("pvp-klondike-block").style.display = pvpMode === "klondike" ? "" : "none";\n'
    '  if(typeof window.klOnVisibility === "function") window.klOnVisibility();\n', before=False)
ins('  if(screenId !== "pvp-screen") stopPvpPolling();\n',
    '  if(typeof window.klOnVisibility === "function") window.klOnVisibility();\n', before=False)
idx = s.rfind("\n</script>")
s = s[:idx] + "\n" + rd("kl.js") + s[idx:]
open(dst, "w", encoding="utf8", newline="").write(s)
print("patched", len(s))
