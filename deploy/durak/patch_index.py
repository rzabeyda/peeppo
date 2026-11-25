"""Adds the «Дурак» button + css/js includes to webapp/index.html (in place, idempotent).
usage: python patch_index.py /root/peeppo/webapp/index.html"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf8").read()
if 'data-mode="durak"' in s:
    print("already patched"); sys.exit(0)

ICON = ("data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E"
        "%3Crect x='3' y='4' width='12' height='17' rx='2' fill='%23fff' stroke='%23999' stroke-width='.6' transform='rotate(-14 9 12)'/%3E"
        "%3Cpath d='M8.2 9.3c-1 1.3-2.3 2-2.3 3.1 0 .9 1 1.4 1.8.8.2-.2.4-.2.5 0 .7.6 1.8.1 1.8-.8 0-1.1-1.3-1.8-1.8-3.1z' fill='%23d1123f' transform='rotate(-14 9 12)'/%3E"
        "%3Crect x='9' y='3' width='12' height='17' rx='2' fill='%23fff' stroke='%23999' stroke-width='.6' transform='rotate(10 15 11)'/%3E"
        "%3Cpath d='M15 7.5c-2 2.3-3.6 3.4-3.6 5.2 0 1.3 1.6 2 2.8 1.1l-.6 2.4h2.8l-.6-2.4c1.2.9 2.8.2 2.8-1.1 0-1.8-1.6-2.9-3.6-5.2z' fill='%23111' transform='rotate(10 15 11)'/%3E"
        "%3C/svg%3E")
BTN = (f'        <button class="mode-switch-btn" data-mode="durak"><img class="mode-switch-icon" src="{ICON}" alt="Дурак">'
       '<span class="mode-switch-label">Дурак</span></button>\n')

anchor = '        <button class="mode-switch-btn" data-mode="klondike">'
if s.count(anchor) == 1:
    s = s.replace(anchor, BTN + anchor)
else:
    rb = '<span class="mode-switch-label">Red&amp;Black</span></button>\n'
    assert s.count(rb) == 1, "no anchor for button"
    s = s.replace(rb, rb + BTN)

inc = '<link rel="stylesheet" href="/games/durak.css?v=1">\n<script src="/games/durak.js?v=1"></script>\n'
i = s.rfind("</body>")
assert i > 0
s = s[:i] + inc + s[i:]
open(p, "w", encoding="utf8", newline="").write(s)
print("patched", len(s))
