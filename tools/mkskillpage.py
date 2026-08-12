# -*- coding: utf-8 -*-
"""A-3-9「武将自身の初期スキル(S以上)は無条件にskills.htmlページを作成する」の一括処理。

内容はその武将のエントリ(skillDetail/trTable/synthesisTable)からの転記なので、
新しい事実を持ち込まない。判断が要るのは次の3点だけで、いずれも機械的に決まる:
  slot               … その武将のsynthesisTableで自分の初期スキルが載っている枠(無ければ"移植不可")
  ownHiddenCandidate … 同じ枠のafterSkill/afterRank
  categoryLinks      … A-3-9の9項目チェックをtarget/skillDetailの語で判定

 python mkskillpage.py            … 作る予定の一覧を表示
 python mkskillpage.py --apply    … skills.html / LINKED_SKILLS / 一覧ページに反映
"""
import io, os, re, sys, collections
sys.path.insert(0, r'c:\Users\uesug\ixa-simulator\tools')
sys.stdout.reconfigure(encoding="utf-8")
from audit_characters import load
from jsedit import close_bracket

ROOT = r'c:\Users\uesug\ixa-simulator'
APPLY = '--apply' in sys.argv
RANK_OK = {'S', 'SS', 'SSS', 'X', 'XX', 'XXX'}

# A-3-9 の9項目 → (キーワード, 一覧ページ, ラベル)
CATS = [
    ('無尽', 'skills-mujin.html', '無尽スキル一覧を見る'),
    ('撤退', 'skills-taitai.html', '撤退スキル一覧を見る'),
    ('覇道', 'skills-hadou.html', '覇道スキル一覧を見る'),
    ('不屈', 'skills-fukutsu.html', '不屈スキル一覧を見る'),
    ('兵station', None, None),  # placeholder(使わない)
    ('兵站', 'skills-heitan.html', '兵站スキル一覧を見る'),
    ('卓越', 'skills-takuetsu.html', '卓越スキル一覧を見る'),
]

D = load()
have = {s['name'] for s in D['skills']}
plan = []
for key, db in (('generals', None), ('kyokuGenerals', 'kyoku')):
    for g in D[key]:
        if not g.get('imageFull'):
            continue
        sk = g.get('initialSkill')
        if not sk or sk in have:
            continue
        sd = g.get('skillDetail') or ''
        m = re.match(r'([A-Z]+)/', sd)
        rank = m.group(1) if m else None
        if rank not in RANK_OK:
            continue
        # 自分の初期スキルが載っている枠
        slots = [r['slot'] for r in (g.get('synthesisTable') or []) if r.get('skill') == sk]
        slot = '・'.join(slots) if slots else '移植不可'
        own = None
        for r in (g.get('synthesisTable') or []):
            if r.get('skill') == sk and r.get('afterSkill'):
                own = (r['afterSkill'], r.get('afterRank'))
                break
        # target / baseRate はskillDetailのヘッダーから
        tm = re.search(r'/対象:([^\n]*)', sd.split('\n')[0])
        target = tm.group(1).replace('\u3000', ' ').strip() if tm else (g.get('troop') or '')
        rm = re.search(r'確率\s*(\d+)%', sd.split('\n')[0])
        rate = int(rm.group(1)) if rm else None
        cats = []
        for word, href, label in CATS:
            if href and (word in target or word in sd):
                cats.append((href, label))
        plan.append(dict(no=g['no'], gname=g['name'], db=db, skill=sk, rank=rank,
                         slot=slot, own=own, target=target, rate=rate,
                         summary=sd, tr=g.get('trTable') or [],
                         noMimic=('模倣不可' in sd), cats=cats))

print('作成対象(S以上・ページ未作成):', len(plan))
for p in plan:
    print('  %-4s %-6s %-16s %-12s slot=%-8s own=%-14s cats=%s' % (
        p['rank'], p['no'], p['gname'], p['skill'], p['slot'],
        (p['own'][0] if p['own'] else '-'), ','.join(c[0].replace('skills-', '').replace('.html', '') for c in p['cats']) or '-'))

if not APPLY:
    sys.exit(0)

# ---- skills.html へ追記 ----
P = os.path.join(ROOT, 'skills.html')
s = io.open(P, encoding='utf-8', newline='').read()
nl = '\r\n' if '\r\n' in s else '\n'
i = s.index('const skills = [')
end = close_bracket(s, s.index('[', i))
blocks = []
for p in plan:
    L = []
    L.append('    {')
    L.append('      // No.%s(%s)自身の初期スキル。A-3-9「武将自身の初期スキル(S以上)は無条件にページを作成する」'
             % (p['no'], p['gname']))
    L.append('      //  に従って2026-08-12に一括作成。内容は%s#%s のskillDetail/trTableからの転記。'
             % ('characters-kyoku.html' if p['db'] else 'characters.html', p['no']))
    head = '      name:"%s", rank:"%s"' % (p['skill'], p['rank'])
    if p['rate'] is not None:
        head += ', baseRate:%d' % p['rate']
    head += ', target:"%s"' % p['target']
    if p['noMimic']:
        head += ', noMimic:true'
    head += ','
    L.append(head)
    L.append('      effectSummary:"%s",' % p['summary'].replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n'))
    if p['cats']:
        L.append('      categoryLinks:[')
        L.append((',' + nl).join('        {href:"%s", label:"%s"}' % c for c in p['cats']))
        L.append('      ],')
    L.append('      sourceCharacters:[')
    L.append('        {name:"%s", no:"%s", slot:"%s"%s}' % (
        p['gname'], p['no'], p['slot'], ', db:"kyoku"' if p['db'] else ''))
    L.append('      ],')
    if p['own']:
        L.append('      ownHiddenCandidate: {skill:"%s", rank:"%s"},' % (p['own'][0], p['own'][1]))
    L.append('      trTable:[')
    rows = [r for r in p['tr'] if r.get('effect')]
    L.append((',' + nl).join('        {level:"%s", points:"%s", effect:"%s"}' % (
        r['level'], r.get('points', '-'), r['effect'].replace('"', '\\"')) for r in rows))
    L.append('      ]')
    L.append('    }')
    blocks.append(nl.join(L))
s = s[:end] + ',' + nl + (',' + nl).join(blocks) + nl + '  ' + s[end:]
io.open(P, 'w', encoding='utf-8', newline='').write(s)
print('skills.html に', len(blocks), '件追加')

# ---- LINKED_SKILLS ----
for f, var in (('characters.html', 'LINKED_SKILLS'), ('characters-kyoku.html', 'KK_LINKED_SKILLS')):
    q = os.path.join(ROOT, f)
    t = io.open(q, encoding='utf-8', newline='').read()
    m = re.search(r'const ' + var + r' = \[(.*?)\];', t, re.S)
    cur = re.findall(r"'([^']+)'", m.group(1))
    add = [p['skill'] for p in plan if p['skill'] not in cur]
    if not add:
        continue
    lit = 'const %s = [%s];' % (var, ', '.join("'%s'" % x for x in cur + add))
    io.open(q, 'w', encoding='utf-8', newline='').write(t[:m.start()] + lit + t[m.end():])
    print(f, len(cur), '→', len(cur) + len(add))
