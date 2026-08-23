import os as _os
import sys as _sys
# リポジトリの根はこのファイルの位置から求める(決め打ちにするとCIやworktreeで壊れる)
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
HERE = _os.path.join(ROOT, "tools", "register", "_work")
_os.makedirs(HERE, exist_ok=True)
_sys.path.insert(0, _os.path.join(ROOT, "tools", "register"))
# -*- coding: utf-8 -*-
"""regbuild.py の下書きを仕上げて data/busho*/{No}.json に書き込む。

埋めるもの: troop / effect / skillDetail / notes、および候補スキルの効果文の再取得。
青丸(reviewedOk無し)で登録する。効果文が全部埋まった武将だけ後で黄丸にする。
"""
import collections
import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")


sys.path.insert(0, HERE)
from regbuild import build, kyoku_dir, load_skill, short_of  # noqa: E402

AXIS = {"攻": "攻", "防": "防", "特": "", "攻防": "攻", "攻速": "攻",
        "攻破": "攻", "防速": "防", "防破": "防", "速": "攻", "攻防破": "攻"}


def label_axis(skill_label):
    m = re.match(r"^([^：]+)：", skill_label or "")
    return AXIS.get(m.group(1), "") if m else ""


def finish(no):
    entry, missing = build(no)
    draft = json.load(io.open(os.path.join(HERE, "draft_%s.json" % no), encoding="utf-8"))
    ix, sk = draft.get("ixanary", {}), draft.get("skill", {})

    # 候補スキルの効果文を、今あるデータで埋め直す
    for r in entry["synthesisTable"]:
        for pre, key in (("", "skill"), ("after", "afterSkill")):
            nm = r.get(key)
            if not nm:
                continue
            j = load_skill(nm)
            if not j:
                continue
            tk = "target" if not pre else "afterTarget"
            rk = "rate" if not pre else "afterRate"
            ek = "effectShort" if not pre else "afterEffectShort"
            if r.get(tk) is None:
                r[tk] = j.get("target")
            if r.get(rk) is None and j.get("baseRate") is not None:
                r[rk] = "%g%%" % j["baseRate"]
            if r.get(ek) is None:
                r[ek] = short_of(j)
        # 移植前後が同じ枠は after* を持たせない(S-09)
        if r.get("afterSkill") == r.get("skill"):
            for k in ("afterTarget", "afterRate", "afterEffectShort"):
                r.pop(k, None)

    tr0 = entry["trTable"][0] if entry["trTable"] else None
    axis = label_axis(ix.get("skillLabel"))
    head_target = None
    body = None
    if tr0:
        m = re.match(r"^(.+?)　確率 ([\d.]+)% / (.+)$", tr0["effect"])
        if m:
            head_target, body = m.group(1), m.group(3)
    # 2026-08-23: troop は「弓砲防」のように区切り無しで書く決まりなので、
    # 対象に入っている中黒・スラッシュ・空白を落としてから軸を足す。
    # 2026-08-23: 対象の「飛翔1」「不屈2」「無尽3」は兵種ではなく対象に付く属性。
    # 落とさずに繋げると troop が「全飛翔1」になって兵種の絞り込みから外れる
    # (No.1817 佐吉 で発覚)。対象そのものは skillDetail に残る。
    _t = re.sub(r"(?:飛翔|不屈|無尽)\d*", "", head_target or "全")
    _t = re.sub(r"[・/\s]", "", _t) or "全"
    entry["troop"] = (_t + axis) if head_target else None
    entry["effect"] = body
    if tr0 and body:
        m = re.match(r"^(.+?)　確率 ([\d.]+)%", tr0["effect"])
        # 「攻撃 43%上昇」→「①攻撃が43%上昇する」。それ以外はそのまま①に置く
        mm = re.match(r"^(攻撃|防御|速度|破壊)\s+(.+上昇)$", body)
        line = ("%sが%sする" % (mm.group(1), mm.group(2))) if mm else body
        # 2026-08-23: 対象の区切りは中黒。ixanary は「弓/砲」と書くので直す。
        # 2026-08-23: 段の名前を "LV10" と決め打ちしていた。童(1800番台)は
        # レベルを持たず「固定」の1段だけなので、実際の段名を使う。
        entry["skillDetail"] = ("%s/%s 確率 %s%% %s/対象:%s\n①%s"
                                % (sk.get("rank") or "-", tr0.get("level") or "LV10",
                                   m.group(2), body,
                                   (head_target or "").replace("/", "・"), line))
    entry["notes"] = [
        "ixanary.com(cards/%s と skills/%s)およびixawiki(BushoCard/%s%s)で登録(2026-08-14)。"
        % (no, entry["initialSkill"], no, entry["name"]),
        "統率はカード画像のバッジで確認し、ixawikiのカードページと一致することを見た(D-01)。",
        "指揮兵数はixanaryの★0-0の値(B-01の+0.5版の罠を避けるため突破ランク別の値は使わない)。",
        "合成候補はixanaryのスキルページの1次(移植前)・2次(移植後)。",
        "章は情報源に記載が無いため未確認。効果文が埋まっていない候補が残るため青丸のまま(D-15)。",
    ]
    d = kyoku_dir(no)
    p = os.path.join(ROOT, "data", d, "%s.json" % no)
    io.open(p, "w", encoding="utf-8", newline="\n").write(
        json.dumps(entry, ensure_ascii=False, indent=1) + "\n")
    blank = sum(1 for r in entry["synthesisTable"] if not r.get("effectShort"))
    print("    → %s/%s.json  troop=%s 効果文なしの枠=%d" % (d, no, entry["troop"], blank))
    return blank


if __name__ == "__main__":
    tot = 0
    for n in sys.argv[1:]:
        tot += finish(n)
    print()
    print("効果文が埋まっていない枠の合計 %d" % tot)
