# -*- coding: utf-8 -*-
"""S-07: 一覧ページ(skills-*.html)に、まだ行が無いスキルを足す。

S-06 は既にある行の sourceCharacters を正本に合わせるだけで、**行そのものを
足す処理が無かった**。そのためスキル側に categoryLinks を書いても一覧ページ
本体には出ないままになり、74件たまっていた(2026-08-16 発覚)。

一覧ページは skills.html と違って生成物ではなく、ページごとに固有の列
(卓越の効果欄・飛翔値・コスト減少量・係数など)を持っている。ここでは
正本 data/skill/{名前}.json の効果文からその列を取り出す。
**取り出せない列があった行は足さずに報告する。** 半端な行を入れて
一覧に「-」が並ぶより、足りないことが分かるほうがよい。
"""
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILLDIR = os.path.join(ROOT, "data", "skill")

# ページごとに、共通の6列(name/skillPage/baseRate/target/lv10Effect/sourceCharacters)
# に加えて必要な列。ここに無いページは共通6列だけで足せる。
EXTRA = {
    "skills-takuetsu.html": ["takuetsuEffect"],
    "skills-hishou-atk.html": ["hishouValue"],
    "skills-hishou-def.html": ["hishouValue"],
    "skills-fukutsu.html": ["fukutsuEffect"],
    "skills-higai.html": ["reduceEffect"],
    "skills-mimic.html": ["tr5Effect"],
    "skills-leadermimic.html": ["tr5Effect"],
    "skills-cost.html": ["costReduction"],
    "skills-cost-atk.html": ["costReduction"],
    "skills-cost-def.html": ["costReduction"],
    "skills-mujin-def.html": ["costReduction"],
    "skills-count-atk.html": ["fixedBonus", "lv10Coefficient",
                              "trMaxCoefficient", "trMaxLabel"],
    "skills-count-def.html": ["fixedBonus", "lv10Coefficient",
                              "trMaxCoefficient", "trMaxLabel"],
}
# 文字列で囲まずに書く列(数値)
NUMERIC = {"fixedBonus", "lv10Coefficient", "trMaxCoefficient",
           "threshold", "thresholdMultiplier"}


def _rows(js):
    """レベル → 効果文。"""
    out = {}
    for r in js.get("trTable") or []:
        if r.get("effect"):
            out[r.get("level")] = r["effect"]
    return out


def _body(effect):
    """「全　確率 32% / 攻撃 160%上昇」の、確率より後ろだけを返す。"""
    if not effect:
        return None
    m = re.search(r"/\s*(.+)$", effect)
    return (m.group(1) if m else effect).strip()


def _top_level(js):
    r = _rows(js)
    return r.get("LV10") or r.get("LV1")


def _tr_max(js):
    """一番進んだ鍛錬段の (ラベル, 効果文)。段が無ければ (None, None)。"""
    r = _rows(js)
    for lv in ("TR5", "TR4", "TR3", "TR2", "TR1"):
        if lv in r:
            return lv, r[lv]
    return None, None


def _num_after(text, *pats):
    for p in pats:
        m = re.search(p, text or "")
        if m:
            return m.group(1)
    return None


def extract(page, name, js):
    """一覧ページ1行分の項目を作る。作れない列があれば (None, 理由)。"""
    lv10 = _top_level(js)
    if not lv10:
        return None, "LV10の効果文が無い"
    row = {
        "name": name,
        "skillPage": "skills.html#" + name,
        "baseRate": js.get("baseRate") or "-",
        "target": js.get("target") or "-",
        "lv10Effect": _body(lv10),
    }
    # effectSummary は改行を含む。改行をまたいで拾うと、隣の行の文まで
    # 巻き込んで壊れた行を書いてしまう(2026-08-16に実際に3ページ壊した)。
    # 改行は「/」に潰し、以降の抜き出しでは区切りとして扱う。
    all_text = re.sub(r"[\r\n]+", " / ",
                      " / ".join(list(_rows(js).values())
                                 + [js.get("effectSummary") or ""]))
    all_text = re.sub(r"[ \t]+", " ", all_text)

    for col in EXTRA.get(page, []):
        if col == "hishouValue":
            # 「飛翔4」と「飛翔+10」の両方の書き方がある
            v = _num_after(all_text, r"飛翔\s*\+?\s*(\d+)")
            if not v:
                return None, "飛翔の数値が効果文から取れない"
            row[col] = v
        elif col == "fukutsuEffect":
            v = _num_after(all_text, r"不屈\s*(\d+)")
            if v:
                row[col] = "不屈" + v
            else:
                # 「不屈(部隊数-2)」のように数でなく式で書かれることがある
                m = re.search(r"不屈\s*([(（][^)）]*[)）])", all_text)
                if not m:
                    return None, "不屈の数値が効果文から取れない"
                row[col] = "不屈" + m.group(1)
        elif col == "costReduction":
            v = _num_after(all_text, r"コスト[消費]*\s*[-−]\s*([\d.]+)",
                           r"部隊消費コスト\s*[-−]\s*([\d.]+)")
            if v:
                row[col] = "-" + v
            else:
                # 「自部隊同時攻撃時この武将分をコストから除外」のように、
                # 減少量ではなく条件で書かれるものがある
                m = re.search(r"([^(（)）。/]*コスト[^(（)）。/]*除[外去])", all_text)
                if not m:
                    return None, "コスト減少量が効果文から取れない"
                row[col] = m.group(1).strip("・、 ")
        elif col == "takuetsuEffect":
            m = re.search(r"卓越[:：]?\s*([^)）。/]*)", all_text)
            if not m or not m.group(1).strip():
                return None, "卓越の効果が効果文から取れない"
            row[col] = m.group(1).strip("・、 ")
        elif col == "reduceEffect":
            m = re.search(r"(兵士被害[^)）。/]*)", all_text)
            if not m:
                return None, "兵士被害の軽減内容が取れない"
            row[col] = m.group(1).strip("・、 ")
        elif col == "tr5Effect":
            _, tr = _tr_max(js)
            # 鍛錬の段が無いスキルは既存の行でも "TRなし" と書いている
            row[col] = _body(tr) if tr else "TRなし"
        elif col in ("fixedBonus", "lv10Coefficient", "trMaxCoefficient", "trMaxLabel"):
            lab, tr = _tr_max(js)
            coef = (r"([\d.]+)\s*%?\s*[×x]\s*(?:自軍|自部隊|部隊内|防御参加|攻撃参加|敵軍)",
                    r"係数[はが]?\s*LV10\s*=\s*([\d.]+)")
            c10 = _num_after(lv10, *coef)
            fixed = _num_after(lv10, r"(?:攻撃|防御|破壊)\s*([\d.]+)\s*%\s*上昇")
            if c10 is None and fixed is None:
                return None, "LV10の係数も固定値も効果文から取れない"
            row["fixedBonus"] = fixed or "0"
            row["lv10Coefficient"] = c10 or "0"
            row["trMaxCoefficient"] = _num_after(tr or "", *coef) or c10 or "0"
            row["trMaxLabel"] = lab or "TRなし"
            # 「〜が120以下の時、効果1.5倍」のような条件つきの倍率。
            # **ページ側の threshold は「以下」の意味しか持たない**
            # (skills-count-def.html の count <= s.threshold)。
            # 「30以上の時」は条件が逆なので入れない。入れると人数が少ないときに
            # 倍率がかかる、実際と真逆の表になる。倍率は lv10Effect の文で伝える。
            th = re.search(r"(\d+)\s*(?:人)?\s*以下の時[、,]?\s*"
                           r"(?:攻撃|防御|破壊)?効果\s*(?:が\s*)?([\d.]+)\s*倍", lv10)
            if th:
                row["threshold"] = th.group(1)
                row["thresholdMultiplier"] = th.group(2)
    return row, None


def render(row, srcs, indent="    "):
    """1行分のJavaScriptを組み立てる。"""
    inn = indent + "  "

    def val(k):
        v = row[k]
        return str(v) if k in NUMERIC else '"%s"' % v

    head = '%s{name:"%s", skillPage:"%s", baseRate:"%s", target:"%s",' % (
        indent, row["name"], row["skillPage"], row["baseRate"], row["target"])
    lines = [head]
    for k in row:
        if k in ("name", "skillPage", "baseRate", "target", "lv10Effect"):
            continue
        lines.append("%s%s:%s," % (inn, k, val(k)))
    lines.append('%slv10Effect:"%s",' % (inn, row["lv10Effect"]))
    lines.append("%ssourceCharacters:[" % inn)
    body = []
    for c in srcs:
        s = '%s  {name:"%s", no:"%s", slot:"%s"' % (
            inn, c.get("name"), c.get("no"), c.get("slot"))
        if c.get("db"):
            s += ', db:"%s"' % c["db"]
        body.append(s + "}")
    lines.append(",\n".join(body))
    lines.append("%s]}" % inn)
    return "\n".join(lines)


def find_missing():
    """(ページ, スキル名) で、categoryLinks はあるのに行が無いもの。"""
    pages = {}
    for p in sorted(os.listdir(ROOT)):
        if p.startswith("skills-") and p.endswith(".html"):
            t = io.open(os.path.join(ROOT, p), encoding="utf-8", newline="").read()
            pages[p] = {m.group(1) for m in
                        re.finditer(r'\{name:"([^"]+)", skillPage:"', t)}
    out = []
    for fn in sorted(os.listdir(SKILLDIR)):
        if not fn.endswith(".json"):
            continue
        js = json.load(io.open(os.path.join(SKILLDIR, fn), encoding="utf-8"))
        for c in js.get("categoryLinks") or []:
            h = c.get("href")
            if h in pages and fn[:-5] not in pages[h]:
                out.append((h, fn[:-5], js))
    return out


def main(write=False):
    sys.stdout.reconfigure(encoding="utf-8")
    missing = find_missing()
    ok, ng = {}, []
    for page, name, js in missing:
        row, why = extract(page, name, js)
        if row is None:
            ng.append((page, name, why))
            continue
        ok.setdefault(page, []).append((row, js.get("sourceCharacters") or []))

    for page in sorted(ok):
        print("  S-07 %-24s %d件 足せる: %s"
              % (page, len(ok[page]), "、".join(r[0]["name"] for r in ok[page])))
    if ng:
        print("\n  足せなかった %d件(列が効果文から取れない)" % len(ng))
        for page, name, why in ng:
            print("    %-24s %-14s %s" % (page, name, why))

    if not write:
        print("\n(--write を付けると書き込む)")
        return
    n = 0
    for page in sorted(ok):
        fp = os.path.join(ROOT, page)
        t = io.open(fp, encoding="utf-8", newline="").read()
        for row, srcs in ok[page]:
            last = None
            for m in re.finditer(r'\{name:"[^"]+", skillPage:"', t):
                last = m
            if last is None:
                print("    %-24s 挿し込む場所が分からない" % page)
                continue
            # 配列の閉じ ']' の直前に足す
            depth, i = 0, last.start()
            while i < len(t):
                if t[i] == "[":
                    depth += 1
                elif t[i] == "]":
                    if depth == 0:
                        break
                    depth -= 1
                elif t[i] == '"':
                    i = t.index('"', i + 1)
                i += 1
            head = t[:i].rstrip()
            t = head + ",\n" + render(row, srcs) + "\n  " + t[i:]
            n += 1
        io.open(fp, "w", encoding="utf-8", newline="").write(t)
    print("\nS-07 一覧ページに %d行 足した" % n)


if __name__ == "__main__":
    main("--write" in sys.argv)
