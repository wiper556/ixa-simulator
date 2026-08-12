# -*- coding: utf-8 -*-
"""A-3-11 逆引き漏れの一括補完。
武将の synthesisTable の skill 列に載っているのに、skills.html 側の
sourceCharacters にその武将が入っていないケースを検出して追記する。

 python fix_srcchars.py            … 検出だけ
 python fix_srcchars.py --apply    … skills.html に追記する
"""
import io, os, re, sys, collections
sys.path.insert(0, r'c:\Users\uesug\ixa-simulator\tools')
sys.stdout.reconfigure(encoding="utf-8")
from audit_characters import load
from jsedit import close_bracket, insert_elements

ROOT = r'c:\Users\uesug\ixa-simulator'
APPLY = '--apply' in sys.argv

D = load()
skills = {s['name']: s for s in D['skills']}

need = collections.OrderedDict()
for key in ('generals', 'kyokuGenerals'):
    for g in D[key]:
        if not g.get('imageFull'):
            continue
        slots = collections.OrderedDict()
        for row in g.get('synthesisTable') or []:
            sk = row.get('skill')
            if sk in skills:
                slots.setdefault(sk, []).append(row.get('slot'))
        for sk, sl in slots.items():
            if not any(c.get('no') == g['no'] for c in (skills[sk].get('sourceCharacters') or [])):
                need.setdefault(sk, []).append((g['name'], g['no'], sl))

print('追記が必要なスキル:', len(need), '/ 追記行数:', sum(len(v) for v in need.values()))
if not APPLY:
    for sk, v in need.items():
        print('  %-16s %s' % (sk, ' '.join('%s(No.%s,%s)' % (n, no, '・'.join(x for x in sl if x)) for n, no, sl in v)))
    sys.exit(0)

p = os.path.join(ROOT, 'skills.html')
s = io.open(p, encoding='utf-8', newline='').read()
nl = '\r\n' if '\r\n' in s else '\n'
targets, skipped, added = [], [], 0
for sk, v in need.items():
    i = s.find('name:"%s", rank:' % sk)
    if i < 0:
        skipped.append((sk, 'エントリ無し')); continue
    j = s.find('sourceCharacters:[', i)
    nxt = s.find(nl + '      name:"', i + 10)
    if j < 0 or (nxt > 0 and j > nxt):
        skipped.append((sk, 'sourceCharacters 無し')); continue
    op = j + len('sourceCharacters:')
    k = close_bracket(s, op)
    if k < 0:
        skipped.append((sk, '閉じ括弧無し')); continue
    targets.append((op, k, v))

for op, k, v in sorted(targets, reverse=True):
    lines = ['{name:"%s", no:"%s", slot:"%s"}' % (n, no, '・'.join(x for x in sl if x)) for n, no, sl in v]
    added += len(lines)
    s = insert_elements(s, op, k, lines, nl)

io.open(p, 'w', encoding='utf-8', newline='').write(s)
print('追記完了:', added, '行 / スキップ:', skipped)
