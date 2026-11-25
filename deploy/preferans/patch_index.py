"""Adds the «Преферанс» button + css/js includes to webapp/index.html (in place, idempotent).
usage: python patch_index.py /root/peeppo/webapp/index.html"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf8").read()
if 'data-mode="pref"' in s:
    print("already patched"); sys.exit(0)

ICON = ("data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E"
        "%3Crect x='1.5' y='5' width='10' height='15' rx='2' fill='%23fff' stroke='%23999' stroke-width='.6' transform='rotate(-18 6.5 12.5)'/%3E"
        "%3Crect x='7' y='3' width='10' height='15' rx='2' fill='%23fff' stroke='%23999' stroke-width='.6'/%3E"
        "%3Crect x='12.5' y='5' width='10' height='15' rx='2' fill='%23fff' stroke='%23999' stroke-width='.6' transform='rotate(18 17.5 12.5)'/%3E"
        "%3Cpath d='M17.5 9.2l2.3 3.3-2.3 3.3-2.3-3.3z' fill='%23d1123f' transform='rotate(18 17.5 12.5)'/%3E"
        "%3Ctext x='12' y='12.5' font-size='6.5' font-weight='bold' text-anchor='middle' fill='%237c3aed' font-family='Arial'%3E10%3C/text%3E"
        "%3C/svg%3E")
BTN = (f'        <button class="mode-switch-btn" data-mode="pref"><img class="mode-switch-icon" src="{ICON}" alt="Преферанс">'
       '<span class="mode-switch-label">Преферанс</span></button>\n')

anchor = '        <button class="mode-switch-btn" data-mode="klondike">'
durak = '<span class="mode-switch-label">Дурак</span></button>\n'
if s.count(durak) == 1:
    s = s.replace(durak, durak + BTN)
elif s.count(anchor) == 1:
    s = s.replace(anchor, BTN + anchor)
else:
    rb = '<span class="mode-switch-label">Red&amp;Black</span></button>\n'
    assert s.count(rb) == 1, "no anchor for button"
    s = s.replace(rb, rb + BTN)

inc = '<link rel="stylesheet" href="/games/preferans.css?v=1">\n<script src="/games/preferans.js?v=1"></script>\n'
i = s.rfind("</body>")
assert i > 0
s = s[:i] + inc + s[i:]
open(p, "w", encoding="utf8", newline="").write(s)
print("patched", len(s))
