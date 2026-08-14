import os as _os
import sys as _sys
# リポジトリの根はこのファイルの位置から求める(決め打ちにするとCIやworktreeで壊れる)
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
HERE = _os.path.join(ROOT, "tools", "register", "_work")
_os.makedirs(HERE, exist_ok=True)
_sys.path.insert(0, _os.path.join(ROOT, "tools", "register"))
# -*- coding: utf-8 -*-
"""登録待ちの武将を、作業しやすい順に並べて出す。

順番の考え方:
  ・generalGrowthDB に既にある = シミュレーターでは使えるのにDBページが無い → 先に埋める
  ・その中でカードNo.の大きい順(新しいもの優先)
"""
import glob
import json
import os
import re
import sys

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")

reg = set()
for d in ("busho", "busho-kyoku", "busho-kyoku-ps", "busho-ketsu"):
    for p in glob.glob(os.path.join(ROOT, "data", d, "*.json")):
        reg.add(os.path.basename(p)[:-5])

imgs = set()
for p in glob.glob(os.path.join(ROOT, "assets", "img", "characters", "no*_full.png")):
    m = re.match(r"no(\d+)_full\.png", os.path.basename(p))
    if m:
        imgs.add(m.group(1))

with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page()
    pg.goto("file:///c:/Users/uesug/ixa-simulator/attack-simulator.html", wait_until="load")
    pg.wait_for_timeout(1300)
    g = pg.evaluate("()=>JSON.parse(JSON.stringify(window.generalGrowthDB||generalGrowthDB))")
    b.close()
gdb = {str(e["no"]): e for e in g}


def rarity(n):
    if len(n) == 5 and n[:2] in ("20", "21", "22"):
        return "傑"
    if len(n) == 4 and n[0] == "3":
        return "特"
    if len(n) == 4 and n[0] in ("2", "7"):
        return "極"
    return "天"


todo = [n for n in (imgs - reg) if rarity(n) in ("極", "特")]
have = sorted([n for n in todo if n in gdb], key=lambda x: -int(x))
lack = sorted([n for n in todo if n not in gdb], key=lambda x: -int(x))

print("登録待ち(極・特) %d件" % len(todo))
print("  うちgeneralGrowthDBにあるもの %d件 / 無いもの %d件" % (len(have), len(lack)))
print()
print("=== 先に片付ける(generalGrowthDBにあるもの、No.降順) ===")
for n in have:
    e = gdb[n]
    print("%-6s %-22s cost=%-5s skill=%-14s rank=%s"
          % (n, e.get("name"), e.get("cost"), e.get("initialSkill"),
             "あり" if e.get("rankGrades") else "無し"))
print()
print("=== generalGrowthDBにも無いもの(No.降順、先頭30) ===")
print("  " + " ".join(lack[:30]))
json.dump({"have": have, "lack": lack},
          open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue.json"),
               "w", encoding="utf-8"), ensure_ascii=False)
