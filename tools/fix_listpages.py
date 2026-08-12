# -*- coding: utf-8 -*-
"""skills.html の sourceCharacters と、各 skills-*.html 一覧ページが持つ複製の差分を検出/補完。
A-3-11 追記ルール(2026-08-04)の自動化。

 python fix_listpages.py           … 差分の検出だけ
 python fix_listpages.py --apply   … 一覧ページ側に不足行を追記
"""
import io, os, re, sys, collections
sys.path.insert(0, r'c:\Users\uesug\ixa-simulator\tools')
sys.stdout.reconfigure(encoding="utf-8")
import audit_characters
from audit_characters import extract_array as _extract_array
from jsedit import close_bracket, insert_elements

# 一部の一覧ページは配列リテラル内で定数を参照している(skills-count-atk.html の
# ALL_TROOP_CATEGORIES 等)。評価前にその定義も一緒に読み込ませる。
def extract_array(path, varname):
    try:
        return _extract_array(path, varname)
    except Exception:
        pass
    import json, re as _re
    from quickjs import Context
    html = io.open(path, encoding='utf-8').read()
    pre = '\n'.join(_re.findall(r'^\s*const [A-Z_]+ = \[[^\]]*\];', html, _re.M))
    for s in _re.findall(r'<script>([\s\S]*?)</script>', html):
        m = _re.search(r'(?:const|let|var)\s+' + _re.escape(varname) + r'\s*=\s*\[', s)
        if not m:
            continue
        start = m.end() - 1
        end = close_bracket(s, start)
        ctx = Context()
        ctx.eval(pre)
        return json.loads(ctx.eval('JSON.stringify(' + s[start:end + 1] + ')'))
    raise RuntimeError(varname + ' not found in ' + path)

ROOT = r'c:\Users\uesug\ixa-simulator'
APPLY = '--apply' in sys.argv
PAGES = {
    'skills-cost.html': 'costSkills',
    'skills-count-atk.html': 'countAtkSkills',
    'skills-count-def.html': 'countDefSkills',
    'skills-fukutsu.html': 'fukutsuSkills',
    'skills-hadou.html': 'hadouSkills',
    'skills-heitan.html': 'heitanSkills',
    'skills-higai.html': 'higaiSkills',
    'skills-hishou-atk.html': 'hishouAtkSkills',
    'skills-hishou-def.html': 'hishouDefSkills',
    'skills-leadermimic.html': 'leaderMimicSkills',
    'skills-mimic.html': 'mimicSkills',
    'skills-mujin.html': 'mujinSkills',
    'skills-taitai.html': 'taitaiSkills',
    'skills-takuetsu.html': 'takuetsuSkills',
}




master = {s['name']: s for s in extract_array(os.path.join(ROOT, 'skills.html'), 'skills')}
total = 0
for page, var in PAGES.items():
    arr = extract_array(os.path.join(ROOT, page), var)
    missing = collections.OrderedDict()
    for e in arr:
        m = master.get(e['name'])
        if not m:
            continue
        have = {c.get('no') for c in (e.get('sourceCharacters') or [])}
        for c in (m.get('sourceCharacters') or []):
            if c.get('no') not in have:
                missing.setdefault(e['name'], []).append(c)
    if not missing:
        continue
    n = sum(len(v) for v in missing.values())
    total += n
    print('%-24s 不足 %d行' % (page, n))
    for k, v in missing.items():
        print('    %-14s %s' % (k, ' '.join('%s(No.%s,%s)' % (c['name'], c['no'], c.get('slot', '')) for c in v)))
    if not APPLY:
        continue
    p = os.path.join(ROOT, page)
    s = io.open(p, encoding='utf-8', newline='').read()
    nl = '\r\n' if '\r\n' in s else '\n'
    targets = []
    for name, cs in missing.items():
        i = s.find('{name:"%s"' % name)
        if i < 0:
            i = s.find('name:"%s"' % name)
        if i < 0:
            print('    !! 見つからない', name); continue
        j = s.find('sourceCharacters:[', i)
        if j < 0:
            print('    !! sourceCharacters 無し', name); continue
        k = close_bracket(s, j + len('sourceCharacters:'))
        targets.append((j, k, cs))
    for j, k, cs in sorted(targets, reverse=True):
        new = ['{name:"%s", no:"%s", slot:"%s"}' % (c['name'], c['no'], c.get('slot', '')) for c in cs]
        s = insert_elements(s, j + len('sourceCharacters:'), k, new, nl)
    io.open(p, 'w', encoding='utf-8', newline='').write(s)
print('合計不足', total, '行', '(適用済み)' if APPLY else '(検出のみ)')
