import os as _os
import sys as _sys
# リポジトリの根はこのファイルの位置から求める(決め打ちにするとCIやworktreeで壊れる)
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
HERE = _os.path.join(ROOT, "tools", "register", "_work")
_os.makedirs(HERE, exist_ok=True)
_sys.path.insert(0, _os.path.join(ROOT, "tools", "register"))
# -*- coding: utf-8 -*-
"""今回足した武将まわりの配線をまとめて直す。

  S-01 萬宝航跡のページを作る
  S-05 合成候補スキルの sourceCharacters に武将を足す(逆引き)
  S-04 KP_LINKED_SKILLS に足す
  S-08 新しく作ったスキルページの ownHiddenCandidate を、そのスキルの
       合成テーブルのS1枠の2次から決める
"""
import collections
import io
import json
import os
import re
import sys
import urllib.parse

sys.stdout.reconfigure(encoding="utf-8")


sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
from tools.reslog import fetch_and_log  # noqa: E402
from regfetch import strip, parse_ixanary_skill  # noqa: E402
from regbuild import split_label  # noqa: E402
import skillbuild  # noqa: E402

NOS = sys.argv[1:] or ["7401"]
SKILLDIR = os.path.join(ROOT, "data", "skill")
HIGH = ("S", "SS", "SSS", "X", "XX", "XXX")

# --- S-05: 逆引き ---
added = 0
for no in NOS:
    for d in ("busho-kyoku", "busho-kyoku-ps", "busho"):
        p = os.path.join(ROOT, "data", d, "%s.json" % no)
        if os.path.exists(p):
            break
    ent = json.load(io.open(p, encoding="utf-8"))
    for r in ent["synthesisTable"]:
        for key in ("skill", "afterSkill"):
            nm = r.get(key)
            if not nm:
                continue
            sp = os.path.join(SKILLDIR, nm + ".json")
            if not os.path.exists(sp):
                continue
            js = json.load(io.open(sp, encoding="utf-8"),
                           object_pairs_hook=collections.OrderedDict)
            sc = js.setdefault("sourceCharacters", [])
            if any(str(x.get("no")) == no for x in sc):
                continue
            sc.append(collections.OrderedDict([
                ("name", ent["name"]), ("no", no), ("slot", r["slot"]), ("db", "kyoku"),
                ("note", ["%s(%s)のsynthesisTable %s枠(2026-08-14)"
                          % (ent["name"], no, r["slot"])])]))
            io.open(sp, "w", encoding="utf-8", newline="\n").write(
                json.dumps(js, ensure_ascii=False, indent=1) + "\n")
            added += 1
print("S-05 逆引きを %d件 追記" % added)

# --- S-08: 武将側の afterSkill から ownHiddenCandidate を決める ---
for no in NOS:
    for d in ("busho-kyoku-ps", "busho-kyoku", "busho"):
        fp = os.path.join(ROOT, "data", d, "%s.json" % no)
        if os.path.exists(fp):
            break
    ent = json.load(io.open(fp, encoding="utf-8"))
    for r in ent["synthesisTable"]:
        nm, af = r.get("skill"), r.get("afterSkill")
        if not nm or not af:
            continue
        sp = os.path.join(SKILLDIR, nm + ".json")
        if not os.path.exists(sp):
            continue
        js = json.load(io.open(sp, encoding="utf-8"), object_pairs_hook=collections.OrderedDict)
        if js.get("ownHiddenCandidate"):
            continue
        ap = os.path.join(SKILLDIR, af + ".json")
        rk = json.load(io.open(ap, encoding="utf-8")).get("rank") if os.path.exists(ap) else r.get("afterRank")
        js["ownHiddenCandidate"] = collections.OrderedDict([("skill", af), ("rank", rk)])
        js.setdefault("notes", []).append(
            "ownHiddenCandidateは、このスキルが1次候補として載っている枠(%s %s %s枠)の2次候補から(S-08)。"
            % (no, ent["name"], r["slot"]))
        io.open(sp, "w", encoding="utf-8", newline="\n").write(
            json.dumps(js, ensure_ascii=False, indent=1) + "\n")
        print("  S-08 %-12s → %s %s" % (nm, af, rk))

# --- S-04: KP_LINKED_SKILLS ---
p = os.path.join(ROOT, "characters-kyoku-ps.html")
t = io.open(p, encoding="utf-8", newline="").read()
m = re.search(r"(const KP_LINKED_SKILLS = \[)(.*?)(\];)", t, re.S)
body = m.group(2).rstrip()
want = []
for no in NOS:
    for d in ("busho-kyoku-ps", "busho-kyoku"):
        fp = os.path.join(ROOT, "data", d, "%s.json" % no)
        if os.path.exists(fp):
            e = json.load(io.open(fp, encoding="utf-8"))
            # ページがあるスキルだけを入れる。A以下でページを作らないものを入れると
            # 監査が「LINKED_SKILLSにあるのにskills.htmlに無い」と鳴る(S-02)
            if e.get("initialSkill") and os.path.exists(
                    os.path.join(SKILLDIR, e["initialSkill"] + ".json")):
                want.append(e["initialSkill"])
            for r in e["synthesisTable"]:
                for k in ("skill", "afterSkill"):
                    if r.get(k) and os.path.exists(os.path.join(SKILLDIR, r[k] + ".json")):
                        want.append(r[k])
            break
add = [n for n in dict.fromkeys(want) if n and ("'%s'" % n) not in body]
if add:
    body += "".join(", '%s'" % n for n in add)
    io.open(p, "w", encoding="utf-8", newline="").write(
        t[:m.start()] + m.group(1) + body + m.group(3) + t[m.end():])
print("S-04 KP_LINKED_SKILLS へ %d件 追加: %s" % (len(add), " ".join(add)))
