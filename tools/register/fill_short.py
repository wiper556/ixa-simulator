import os as _os
import sys as _sys
# リポジトリの根はこのファイルの位置から求める(決め打ちにするとCIやworktreeで壊れる)
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
HERE = _os.path.join(ROOT, "tools", "register", "_work")
_os.makedirs(HERE, exist_ok=True)
_sys.path.insert(0, _os.path.join(ROOT, "tools", "register"))
# -*- coding: utf-8 -*-
"""合成候補のうち、うちにページが無いスキル(A以下など)の効果文だけを
ixanaryから取って synthesisTable に入れる。ページは作らない(S-02)。

  python fill_short.py <No> ...
"""
import io
import json
import os
import sys
import urllib.parse

sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, ROOT)

sys.path.insert(0, HERE)
from tools.reslog import fetch_and_log  # noqa: E402
from regfetch import strip, parse_ixanary_skill  # noqa: E402
from skillbuild import parse_level  # noqa: E402
from regbuild import kyoku_dir  # noqa: E402

CACHE = {}


def lookup(name):
    if name in CACHE:
        return CACHE[name]
    raw = fetch_and_log("skill:%s" % name, "ixanary",
                        "https://ixanary.com/skills/" + urllib.parse.quote(name),
                        encoding="utf-8")
    v = (None, None, None)
    if raw:
        d = parse_ixanary_skill(strip(raw))
        lv = [x for x in (d.get("levels") or []) if x["level"] != "LV1"]
        if lv:
            r, tg, body = parse_level(lv[0]["text"])
            v = (tg, ("%g%%" % r) if r is not None else None, body or None)
    CACHE[name] = v
    return v


for no in sys.argv[1:]:
    p = os.path.join(ROOT, "data", kyoku_dir(no), "%s.json" % no)
    j = json.load(io.open(p, encoding="utf-8"),
                  object_pairs_hook=__import__("collections").OrderedDict)
    n = 0
    for r in j["synthesisTable"]:
        for pre in ("", "after"):
            nm = r.get("skill" if not pre else "afterSkill")
            ek = "effectShort" if not pre else "afterEffectShort"
            tk = "target" if not pre else "afterTarget"
            rk = "rate" if not pre else "afterRate"
            if not nm or (pre and r.get("afterSkill") == r.get("skill")):
                continue
            if r.get(ek):
                continue
            tg, rt, body = lookup(nm)
            if body:
                r[tk], r[rk], r[ek] = tg, rt, body
                n += 1
    io.open(p, "w", encoding="utf-8", newline="\n").write(
        json.dumps(j, ensure_ascii=False, indent=1) + "\n")
    blank = sum(1 for r in j["synthesisTable"] if not r.get("effectShort"))
    print("  No.%-6s %-16s 埋めた%d件 / まだ空%d枠" % (no, j["name"], n, blank))
