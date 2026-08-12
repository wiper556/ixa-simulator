# -*- coding: utf-8 -*-
"""skills系ページのJS配列がすべてパースできるか確認する。"""
import io, os, re, sys, json
sys.path.insert(0, r'c:\Users\uesug\ixa-simulator\tools')
sys.stdout.reconfigure(encoding="utf-8")
from jsedit import close_bracket
from quickjs import Context

ROOT = r'c:\Users\uesug\ixa-simulator'
PAGES = {
    'skills.html': 'skills',
    'skills-cost.html': 'costSkills', 'skills-count-atk.html': 'countAtkSkills',
    'skills-count-def.html': 'countDefSkills', 'skills-fukutsu.html': 'fukutsuSkills',
    'skills-hadou.html': 'hadouSkills', 'skills-heitan.html': 'heitanSkills',
    'skills-higai.html': 'higaiSkills', 'skills-hishou-atk.html': 'hishouAtkSkills',
    'skills-hishou-def.html': 'hishouDefSkills', 'skills-leadermimic.html': 'leaderMimicSkills',
    'skills-mimic.html': 'mimicSkills', 'skills-mujin.html': 'mujinSkills',
    'skills-taitai.html': 'taitaiSkills', 'skills-takuetsu.html': 'takuetsuSkills',
    'characters.html': 'generals', 'characters-kyoku.html': 'kyokuGenerals',
}
ng = 0
for page, var in PAGES.items():
    html = io.open(os.path.join(ROOT, page), encoding='utf-8').read()
    pre = '\n'.join(re.findall(r'^\s*const [A-Z_]+ = \[[^\]]*\];', html, re.M))
    done = False
    for s in re.findall(r'<script>([\s\S]*?)</script>', html):
        m = re.search(r'(?:const|let|var)\s+' + re.escape(var) + r'\s*=\s*\[', s)
        if not m:
            continue
        start = m.end() - 1
        end = close_bracket(s, start)
        try:
            ctx = Context(); ctx.eval(pre)
            a = json.loads(ctx.eval('JSON.stringify(' + s[start:end + 1] + ')'))
            holes = sum(1 for x in a if x is None)
            print('OK  %-24s %4d件%s' % (page, len(a), '  ★穴あき%d' % holes if holes else ''))
            if holes:
                ng += 1
        except Exception as e:
            ng += 1
            print('NG  %-24s %s' % (page, str(e).replace('\n', ' ')[:90]))
            em = re.search(r'<input>:(\d+)', str(e))
            if em:
                ln = int(em.group(1))
                body = s[start:end + 1].split('\n')
                for k in range(max(0, ln - 4), min(len(body), ln + 2)):
                    print('      %4d %s' % (k + 1, body[k][:130]))
        done = True
        break
    if not done:
        ng += 1
        print('NG  %-24s 配列が見つからない' % page)
print('NG件数', ng)
