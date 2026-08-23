import os as _os
import sys as _sys
# リポジトリの根はこのファイルの位置から求める(決め打ちにするとCIやworktreeで壊れる)
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
HERE = _os.path.join(ROOT, "tools", "register", "_work")
_os.makedirs(HERE, exist_ok=True)
_sys.path.insert(0, _os.path.join(ROOT, "tools", "register"))
# -*- coding: utf-8 -*-
"""draft_{No}.json から data/busho*/{No}.json を組み立てる。

  python regbuild.py <No> ...

・統率はixawikiのカードページの値を使う(呼ぶ前にカード画像と一致することを目視確認しておく)
・合成候補はixanaryスキルページの1次/2次。1次≠2次なら after* も入れる(S-09)
・候補スキルの対象・確率・効果文はサイト内の既存登録から転記。無ければnull(D-07)
・S以上で data/skill にページが無いものは名前を報告する(S-01の宿題)
"""
import collections
import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")


SKILLDIR = os.path.join(ROOT, "data", "skill")
RANKS = ("XXX", "XX", "X", "SSS", "SS", "S", "A", "B", "C", "D", "E", "F")
SLOTS = ("A", "B", "C", "S1", "S2")


def kyoku_dir(no):
    """カードNo. → data/ の置き場所。

    2026-08-23 追記: もとは 傑・極・天 の3つしか見ておらず、**特・上・城・
    天パラレルが全部 "busho"(天のディレクトリ)に落ちていた。**
    特の 3163〜3167 を登録しようとして5枚とも data/busho/ に書かれて発覚した。
    置き場所はレアリティそのものなので、間違えるとサイトの一覧に別レアとして出る。

    特と特シークレットは No. では見分けにくいが、実データは
    busho-toku=3001〜3689 / busho-toku-s=3701〜3730 で **3700番台が
    シークレット**という分かれ方をしている(ixagno の限定武将一覧とも一致)。
    例外は busho-toku に入っている 3725 と 3728 の2件で、これは
    シークレット側にあるべきものが取り込み時に振り分けを外したとみられる
    (2026-08-23 時点では触っていない。申し送り)。
    """
    n = str(no)
    if len(n) == 5:
        if n[:2] in ("20", "21", "22"):
            return "busho-ketsu"
        if n[:2] == "31":
            return "busho-parallel"
        return "busho"          # 10xxx(記念・コラボ)は天と同じ置き場
    if len(n) == 4:
        if n[0] in ("2", "7"):
            h = (int(n) // 100) % 10
            return "busho-kyoku" if h <= 3 else "busho-kyoku-ps"
        if n[0] == "3":
            return "busho-toku-s" if int(n) >= 3700 else "busho-toku"
        if n[0] == "4":
            return "busho-ue"
        if n[0] == "5":
            return "busho-jo"
    if len(n) == 4 and n[0] == "1" and 1800 <= int(n) <= 1899:
        return "busho-do"          # 童(2026-08-23)
    return "busho"


def split_label(s):
    """「防：國才連豪 SS」→ (國才連豪, SS)"""
    s = s.strip()
    m = re.match(r"^(?:[^：]+：)?(.+?)\s+([A-Z]+)$", s)
    return (m.group(1).strip(), m.group(2)) if m else (s, None)


def load_skill(name):
    p = os.path.join(SKILLDIR, name + ".json")
    return json.load(io.open(p, encoding="utf-8")) if os.path.exists(p) else None


def short_of(j):
    eff = (j.get("trTable") or [{}])[0].get("effect") or ""
    return eff.split("/", 1)[1].strip() if "/" in eff else (eff or None)


def side(slot, name, rank):
    """その枠の片側(移植前 or 移植後)の 対象/確率/効果 を既存登録から引く。"""
    j = load_skill(name)
    if not j:
        return None, None, None
    return (j.get("target"), fmt_rate(j.get("baseRate")), short_of(j))


def fmt_rate(v):
    """baseRate を「NN%」の形にする。

    2026-08-23: `"%d%%" % v` と決め打ちしていたので、数値で表せない確率を持つ
    スキルで落ちていた(特の 3548〜3551 の登録が4枚とも止まった)。
    正本には 伏雷蒼樹 の "卓越50%"(通常確率では発動しない)と
    戦陣 勝鬨 の "35%/100%"(2つの確率を持つ)があり、**どちらも誤りではない**。
    文字列はそのまま通し、小数は .0 を落とす。
    """
    if v is None:
        return None
    if isinstance(v, str):
        return v if v.endswith("%") else v + "%"
    return "%g%%" % v


def build(no):
    d = json.load(io.open(os.path.join(HERE, "draft_%s.json" % no), encoding="utf-8"))
    ix, wk, sk = d.get("ixanary", {}), d.get("ixawiki", {}), d.get("skill", {})
    first = (sk.get("synthesis") or {}).get("first") or []
    second = (sk.get("synthesis") or {}).get("second") or first

    rows, missing = [], []
    for i, slot in enumerate(SLOTS):
        if i >= len(first):
            rows.append(collections.OrderedDict(
                [("slot", slot)] + [(k, None) for k in
                 ("skill", "rank", "afterSkill", "afterRank", "target", "rate", "effectShort")]))
            continue
        bn, br = split_label(first[i])
        an, ar = split_label(second[i]) if i < len(second) else (bn, br)
        t, r, e = side(slot, bn, br)
        row = collections.OrderedDict([
            ("slot", slot), ("skill", bn), ("rank", br),
            ("afterSkill", an), ("afterRank", ar),
            ("target", t), ("rate", r), ("effectShort", e)])
        if an != bn:                       # 移植で別スキルに化ける枠だけ after* を入れる
            at, ar2, ae = side(slot, an, ar)
            row["afterTarget"], row["afterRate"], row["afterEffectShort"] = at, ar2, ae
        for n, rk in ((bn, br), (an, ar)):
            if rk in ("S", "SS", "SSS", "X", "XX", "XXX") and not load_skill(n):
                missing.append("%s(%s)" % (n, rk))
        rows.append(row)

    # ixanaryの生表記(「確率：+70% / 対象 全 防御：580%上昇」)を、うちの書式
    # (「全　確率 70% / 防御 580%上昇」)に直す。skillbuild と同じ変換を使う。
    from skillbuild import parse_level
    tr, tgt0 = [], None
    for lv in (sk.get("levels") or []):
        if lv["level"] == "LV1":
            continue
        r, tg, body = parse_level(lv["text"])
        tgt0 = tgt0 or tg
        head = "%s　確率 %s%%" % (tg or tgt0 or "全", ("%g" % r) if r is not None else "-")
        tr.append(collections.OrderedDict([
            ("level", lv["level"]),
            ("points", {"LV10": "-", "TR1": "10", "TR2": "40", "TR3": "90",
                        "TR4": "150", "TR5": "200", "TR6": "パラレル"}.get(lv["level"], "-")),
            ("effect", "%s / %s" % (head, body) if body else head)]))

    # 2026-08-23: カード画像が無いのにパスを書いていた。特の 3163〜3167 で
    # リンク切れになって発覚。DBの慣例は「画像が無ければ null」
    # (data/busho-toku だけで163件がその形)。実体を見てから決める。
    _full = os.path.join(ROOT, "assets", "img", "characters", "no%s_full.png" % no)
    _char = os.path.join(ROOT, "assets", "img", "characters", "no%s_char.png" % no)
    entry = collections.OrderedDict([
        # 2026-08-23: ch は "未確認" という独自の値を入れていた。DBの慣例は null
        # (busho-toku 243件のうち205件が null、"未確認" は今日の5件だけだった)。
        ("name", ix.get("name")), ("no", str(no)), ("ch", None),
        ("cost", wk.get("cost", ix.get("cost"))),
        ("troop", None), ("sub", ""), ("effect", None),
        ("furigana", ix.get("furigana") or wk.get("furigana")),
        ("illustrator", wk.get("illustrator") or ix.get("illustrator")),
        ("imageFull", "assets/img/characters/no%s_full.png" % no
         if os.path.exists(_full) else None),
        ("imageChar", "assets/img/characters/no%s_char.png" % no
         if os.path.exists(_char) else None),
        ("atkBase", ix.get("atkBase")), ("atkGrowth", ix.get("atkGrowth")),
        ("defBase", ix.get("defBase")), ("defGrowth", ix.get("defGrowth")),
        ("tacticsBase", ix.get("tacticsBase")), ("tacticsGrowth", ix.get("tacticsGrowth")),
        ("lv0Troops", ix.get("lv0Troops")),
        ("rankGrades", collections.OrderedDict(
            [(k, (wk.get("rankGrades") or {}).get(k)) for k in ("yari", "yumi", "uma", "ki")])),
        ("initialSkill", sk.get("skillName")),
        ("skillDetail", None),
        ("trTable", tr), ("synthesisTable", rows),
        ("notes", []),
    ])
    p = os.path.join(HERE, "entry_%s.json" % no)
    io.open(p, "w", encoding="utf-8").write(json.dumps(entry, ensure_ascii=False, indent=1))
    print("No.%-6s %-16s → %s/  cost=%-5s 統率=%s"
          % (no, entry["name"], kyoku_dir(no), entry["cost"],
             "/".join(str(v) for v in entry["rankGrades"].values())))
    print("        スキル %s %s  段=%s" % (entry["initialSkill"], sk.get("rank"),
                                       ",".join(r["level"] for r in tr) or "なし"))
    for r in rows:
        print("        %-3s %-14s %-4s → %-14s %s"
              % (r["slot"], r["skill"], r["rank"], r["afterSkill"],
                 "(既存データあり)" if r["target"] else "(効果文なし)"))
    if missing:
        print("        ★S以上でページ未作成: %s" % " / ".join(sorted(set(missing))))
    return entry, missing


if __name__ == "__main__":
    allmiss = []
    for n in sys.argv[1:]:
        _e, m = build(n)
        allmiss += m
    if allmiss:
        print()
        print("== 要作成のスキルページ(S-01) ==")
        print("  " + " / ".join(sorted(set(allmiss))))
