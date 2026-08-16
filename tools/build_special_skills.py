# -*- coding: utf-8 -*-
"""正本 data/skill/{名前}.json から specialSkills の未登録分を組み立てる。

■なぜ要るか

シミュレーターの specialSkills は手で書いてきたが、スキルの正本が541件に
増えたのに対し、シミュレーターに入っているのは355件だった。**残り260件は
部隊に入れても上昇%が0のまま**で、しかも黙って0になるので気付けない。
手で260件書くのは現実的でないので、効果文から組める分を生成する。

■何を生成して、何を生成しないか

効果文は社内規則で「対象兵科　確率 X%/効果範囲 計算式=結果」に揃えてある。
そこから確実に取れるのは 確率・対象・攻撃/防御/速度/破壊の上昇% だけ。

  生成する  攻撃 X%上昇 / 防御 X%上昇 の形をLV10に持つもの
  生成しない 「4%×防御参加武将数」のような式、模倣系、被害増減、
             LV10の効果文が無いもの

**手で書いた355件には一切触らない。** 卓越テーブル・覇道フラグ・
兵站など、効果文からは出てこない情報が入っている。生成分は
BUILD ブロックとして末尾に足すだけ。

■正しさの確かめ方

--check は、すでに手で書いてある355件を同じパーサに通し、手書きの値と
突き合わせる。**ここが合わないなら、生成分も同じだけ狂っている。**

  python tools/build_special_skills.py --check   # 手書きと突き合わせるだけ
  python tools/build_special_skills.py --write   # 生成ブロックを書き込む
"""
import collections
import glob
import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "assets", "js", "ixa-data.js")
SKILLDIR = os.path.join(ROOT, "data", "skill")

BEGIN = ("  // BUILD:specialSkillsAuto:start ここから下は "
         "tools/build_special_skills.py が data/skill/ から生成しています。"
         "直接編集しないこと")
END = "  // BUILD:specialSkillsAuto:end"

LEVELS = ["LV10", "TR1", "TR2", "TR3", "TR4", "TR5", "TR6"]
KEY = {"LV10": "base", "TR1": "TR1", "TR2": "TR2", "TR3": "TR3",
       "TR4": "TR4", "TR5": "TR5", "TR6": "TR6"}

# 対象兵科の1文字 → シミュレーター側の呼び名。
#
# **「焙」「騎」「鉄」は兵科ではなく兵種。** 砲兵科には鉄砲足軽・騎馬鉄砲・
# 焙烙火矢・雑賀衆の4兵種が居るので、「焙」を砲兵科として扱うと残り3兵種にも
# 効いてしまう。手で書いたエントリはここが揃っておらず、十三ノ奇跡・
# 天弦ノ威軍・百識ノ計の3件だけ砲兵科(=4兵種)になっている(効きすぎている疑い。
# 凶星ノ斬光・天限ノ麒麟・鎮西ノ雷神は兵種指定で正しい)。生成側は狭いほうに揃える。
CAT = {"槍": "槍兵科", "弓": "弓兵科", "馬": "騎馬兵科",
       "器": "兵器兵科(器兵科)", "砲": "兵器兵科(砲兵科)"}
SOLDIER = {"焙": "焙烙火矢", "騎": "騎馬鉄砲", "鉄": "鉄砲足軽"}


def rows_of(j):
    return {r.get("level"): (r.get("effect") or "")
            for r in (j.get("trTable") or [])}


def pct_table(rows, word):
    """「攻撃 350%上昇」のような値を段ごとに拾う。"""
    out = {}
    for lv in LEVELS:
        t = rows.get(lv)
        if not t:
            continue
        m = re.search(word + r"\s*([\d.]+)%上昇", t)
        if m:
            out[KEY[lv]] = float(m.group(1))
    return out if "base" in out else None


def rate_table(rows):
    out = {}
    for lv in LEVELS:
        t = rows.get(lv)
        if not t:
            continue
        m = re.search(r"確率\s*([\d.]+)%", t)
        if m:
            out[KEY[lv]] = float(m.group(1))
    return out


def target_of(j, rows):
    """対象兵科 → (兵科の一覧, 兵種の一覧)。「全」なら制限なしで両方 None。"""
    t = (j.get("target") or "").strip()
    if not t:
        m = re.match(r"^\s*(\S+?)\s*　", rows.get("LV10") or "")
        t = m.group(1) if m else ""
    if not t or "全" in t:
        return None, None
    cats = [CAT[c] for c in CAT if c in t]
    sold = [SOLDIER[c] for c in SOLDIER if c in t]
    return (sorted(set(cats)) or None), (sorted(set(sold)) or None)


# 「攻撃 2.5%×防御参加武将数(280人)=700.0%」「攻撃 100%+12.5%×防御参加武将数…」
# 「攻撃 基礎値340%×部隊内の同スキル所持武将数(4人)=1360%上昇」
# 戦闘ごとに変わる数に比例するタイプ。シミュレーターの variableFormula に載る。
VAR_RE = re.compile(
    r"(攻撃|防御)\s*(?:(?P<base>[\d.]+)%\+)?(?:基礎値)?(?P<per>[\d.]+)%\s*[×x]\s*"
    r"(?P<var>防御参加武将数|部隊内の同スキル所持武将数)")
VAR_NAME = {"防御参加武将数": "defenderCount",
            "部隊内の同スキル所持武将数": "sameSkillCount"}


def var_formula(rows):
    """(statTarget, base, perUnitの段別表, 変数名)。当てはまらなければ None。"""
    m = VAR_RE.search(rows.get("LV10") or "")
    if not m:
        return None
    st = "atk" if m.group(1) == "攻撃" else "def"
    per = {}
    for lv in LEVELS:
        t = rows.get(lv)
        if not t:
            continue
        mm = VAR_RE.search(t)
        if mm and mm.group(1) == m.group(1):
            per[KEY[lv]] = float(mm.group("per"))
    if "base" not in per:
        return None
    return st, float(m.group("base") or 0), per, VAR_NAME[m.group("var")]


def parse(j):
    """効果文から組める分だけを返す。組めなければ None。"""
    rows = rows_of(j)
    if not (rows.get("LV10") or ""):
        return None
    atk = pct_table(rows, "攻撃")
    dfn = pct_table(rows, "防御")
    vf = var_formula(rows)
    if not atk and not dfn and vf:
        st, base, per, var = vf
        d = collections.OrderedDict()
        d["effectAxis"] = st
        d["activationType"] = "triggered"
        d["statTarget"] = st
        d["variableFormula"] = {"statTarget": st, "base": base,
                                "perUnitTable": per, "variable": var}
        # 段階選択のUI用に、変数が0のときの参考値を置く(既存の書き方に合わせる)
        d["trTable"] = {k: base for k in per} if base else dict(per)
        rt = rate_table(rows)
        if rt:
            d["baseRate"] = rt.get("base", list(rt.values())[0])
            if len(set(rt.values())) > 1:
                d["rateTable"] = rt
        cats, sold = target_of(j, rows)
        if cats:
            d["targetTroopCategories"] = cats
        if sold:
            d["targetSoldierNames"] = sold
        if "模倣不可" in (rows.get("LV10") or ""):
            d["noMimic"] = True
        return d
    if not atk and not dfn:
        return None
    d = collections.OrderedDict()
    d["effectAxis"] = "both" if (atk and dfn) else ("atk" if atk else "def")
    d["activationType"] = "triggered"
    d["statTarget"] = "atk" if atk else "def"
    d["trTable"] = atk or dfn
    # 攻撃と防御で数値が違うスキルは、防御側の表を別に持たせる
    if atk and dfn and atk != dfn:
        d["defTrTable"] = dfn
    sp = pct_table(rows, "速度")
    if sp:
        d["speedTrTable"] = sp
    de = pct_table(rows, "破壊")
    if de:
        d["destructTrTable"] = de
    rt = rate_table(rows)
    if rt:
        d["baseRate"] = rt["base"] if "base" in rt else list(rt.values())[0]
        if len(set(rt.values())) > 1:
            d["rateTable"] = rt
    cats, sold = target_of(j, rows)
    if cats:
        d["targetTroopCategories"] = cats
    if sold:
        d["targetSoldierNames"] = sold
    if "模倣不可" in (rows.get("LV10") or "") or "模倣不可" in (j.get("skillDetail") or ""):
        d["noMimic"] = True
    return d


# ---------------- 手書き分の読み出し(--check 用) ----------------
def hand_written(src):
    body = src[src.index("const specialSkills = {"):]
    body = body[:body.index("\n};")]
    if BEGIN in body:
        body = body[:body.index(BEGIN)]
    out = {}
    for m in re.finditer(r"^  '([^']+)':\s*\{(.*?)^  \},?$", body, re.S | re.M):
        nm, blk = m.group(1), m.group(2)
        d = {}
        r = re.search(r"baseRate:\s*(-?[\d.]+)", blk)
        if r:
            d["baseRate"] = float(r.group(1))
        r = re.search(r"effectAxis:\s*'(\w+)'", blk)
        if r:
            d["effectAxis"] = r.group(1)
        r = re.search(r"trTable:\s*\{([^}]*)\}", blk)
        if r:
            d["trTable"] = {k: float(v) for k, v in
                            re.findall(r"(\w+)\s*:\s*(-?[\d.]+)", r.group(1))}
        r = re.search(r"targetTroopCategories:\s*\[([^\]]*)\]", blk)
        if r:
            d["cats"] = sorted(re.findall(r"'([^']+)'", r.group(1)))
        out[nm] = d
    return out


def load_skills():
    out = {}
    for p in sorted(glob.glob(os.path.join(SKILLDIR, "*.json"))):
        j = json.load(io.open(p, encoding="utf-8"))
        out[j["name"]] = j
    return out


def check():
    src = io.open(DATA, encoding="utf-8").read()
    hand = hand_written(src)
    skills = load_skills()
    n = collections.Counter()
    bad = []
    for nm, h in sorted(hand.items()):
        j = skills.get(nm)
        if not j:
            n["正本が無い"] += 1
            continue
        g = parse(j)
        if not g:
            n["解析できない"] += 1
            continue
        if not h.get("trTable"):
            n["手書きにtrTableが無い"] += 1
            continue
        common = set(g["trTable"]) & set(h["trTable"])
        if not common:
            n["段が噛み合わない"] += 1
            continue
        why = []
        if any(abs(g["trTable"][k] - h["trTable"][k]) > 0.001 for k in common):
            why.append("上昇%")
        if (h.get("baseRate") is not None and g.get("baseRate") is not None
                and abs(h["baseRate"] - g["baseRate"]) > 0.001):
            why.append("確率")
        if h.get("effectAxis") and g["effectAxis"] != h["effectAxis"]:
            why.append("軸(手書き%s/生成%s)" % (h["effectAxis"], g["effectAxis"]))
        if "cats" in h and sorted(g.get("targetTroopCategories") or []) != h["cats"]:
            why.append("対象兵科(手書き%s/生成%s)"
                       % (h["cats"], g.get("targetTroopCategories")))
        if why:
            n["食い違い"] += 1
            bad.append((nm, why, h, g))
        else:
            n["一致"] += 1
    tot = n["一致"] + n["食い違い"]
    print("手書き %d件と突き合わせた" % len(hand))
    for k, v in n.most_common():
        print("  %-20s %d件" % (k, v))
    if tot:
        print("  比べられた %d件中 一致 %.1f%%" % (tot, 100.0 * n["一致"] / tot))
    print()
    for nm, why, h, g in bad[:40]:
        print("  %-16s %s" % (nm, " / ".join(why)))
        if "上昇%" in why or "確率" in why:
            print("      手書き 率=%s %s" % (h.get("baseRate"), h.get("trTable")))
            print("      生成   率=%s %s" % (g.get("baseRate"), g.get("trTable")))
    return bad


# ---------------- 生成 ----------------
def num(v):
    return str(int(v)) if float(v) == int(v) else str(v)


def js_table(d):
    order = ["base", "TR1", "TR2", "TR3", "TR4", "TR5", "TR6"]
    return "{ " + ", ".join("%s:%s" % (k, num(d[k]))
                            for k in order if k in d) + " }"


def js_list(v):
    return "[" + ",".join("'%s'" % x for x in v) + "]"


def entry(nm, d, j):
    lines = ["  '%s': {" % nm]
    for k, v in d.items():
        if k in ("trTable", "defTrTable", "speedTrTable", "destructTrTable",
                 "rateTable"):
            lines.append("    %s: %s," % (k, js_table(v)))
        elif k == "variableFormula":
            lines.append("    variableFormula: { statTarget: '%s', base: %s, "
                         "perUnitTable: %s, variable: '%s' },"
                         % (v["statTarget"], num(v["base"]),
                            js_table(v["perUnitTable"]), v["variable"]))
        elif k in ("targetTroopCategories", "targetSoldierNames"):
            lines.append("    %s: %s," % (k, js_list(v)))
        elif isinstance(v, bool):
            lines.append("    %s: true," % k)
        elif isinstance(v, float):
            lines.append("    %s: %s," % (k, num(v)))
        else:
            lines.append("    %s: '%s'," % (k, v))
    lv10 = (rows_of(j).get("LV10") or "").replace("'", "’")
    lines.append("    note: '%s。効果文から自動生成(tools/build_special_skills.py)。'"
                 % (("[%s] " % j.get("rank") if j.get("rank") else "") + lv10)[:300]
                 + " +\n          '正本 data/skill/%s.json。効果文に無い仕組み"
                   "(卓越・覇道・兵站など)は入っていない。'" % nm)
    lines.append("  },")
    return "\n".join(lines)


def write():
    src = io.open(DATA, encoding="utf-8", newline="").read()
    hand = hand_written(src)
    skills = load_skills()
    made, skipped = [], collections.Counter()
    for nm, j in sorted(skills.items()):
        if nm in hand:
            continue
        d = parse(j)
        if not d:
            skipped["効果文から組めない"] += 1
            continue
        made.append(entry(nm, d, j))
    block = BEGIN + "\n" + "\n".join(made) + "\n" + END

    if BEGIN in src:
        s = src.index(BEGIN)
        e = src.index(END) + len(END)
        src = src[:s] + block + src[e:]
    else:
        # specialSkills の閉じ括弧の直前に置く
        head = src.index("const specialSkills = {")
        close = src.index("\n};", head)
        src = src[:close + 1] + block + "\n" + src[close + 1:]
    io.open(DATA, "w", encoding="utf-8", newline="").write(src)
    print("生成 %d件 / 組めずに見送り %d件" % (len(made), skipped["効果文から組めない"]))


if __name__ == "__main__":
    if "--write" in sys.argv:
        write()
    else:
        check()
