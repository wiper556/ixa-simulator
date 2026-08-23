import os as _os
import sys as _sys
# リポジトリの根はこのファイルの位置から求める(決め打ちにするとCIやworktreeで壊れる)
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
HERE = _os.path.join(ROOT, "tools", "register", "_work")
_os.makedirs(HERE, exist_ok=True)
_sys.path.insert(0, _os.path.join(ROOT, "tools", "register"))
# -*- coding: utf-8 -*-
"""武将1体分の情報を標準2ソースから取って、構造化して出す(登録の下書き用)。

  python regfetch.py <No> [<No> ...]

取るもの
  ixanary /cards/{No}/        コスト・指揮(★0-0)・初期値・成長値・ふりがな・絵師・スキル各LVの効果
  ixawiki BushoCard/{No}{名}   統率(槍/馬/弓/器)・スキル・一覧での表記
  ixanary /skills/{スキル名}    LV1〜TR6の効果・合成テーブル(1次/2次)

結果は draft_{No}.json に落とす。統率はカード画像で必ず目視確認すること(D-01)。
"""
import io
import json
import os
import re
import sys
import urllib.parse

sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, ROOT)
from tools.reslog import fetch_and_log  # noqa: E402


TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"[ \t\u3000]+")


def strip(html):
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    t = re.sub(r"(?i)</(tr|div|p|li|table|h\d)>", "\n", t)
    t = re.sub(r"(?i)</t[dh]>", " | ", t)
    t = TAG.sub("", t)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&gt;", ">"), ("&lt;", "<"),
                 ("&#30687;", "矟")):
        t = t.replace(a, b)
    return [WS.sub(" ", x).strip(" |") for x in t.split("\n") if x.strip(" |\t")]


def parse_ixanary_card(lines, no):
    d = {}
    for i, ln in enumerate(lines):
        # 「No.7401 山田長政」だけを拾う(ページ表題は「… 萬宝航跡 | 戦国IXAnary」なので除く)
        m = re.match(r"^No\.%s ([^|]+)$" % no, ln)
        if m and "name" not in d and " " not in m.group(1).strip():
            d["name"] = m.group(1).strip()
            if i + 1 < len(lines) and re.match(r"^[ぁ-んー]+$", lines[i + 1]):
                d["furigana"] = lines[i + 1]
        # 「No | レア | 職業 | 名前 | コスト | 指揮 | スキル」の行。位置で取る
        if ln.startswith(no + " |"):
            f = [x.strip() for x in ln.split("|")]
            if len(f) >= 7 and re.match(r"^[\d.]+$", f[4]) and re.match(r"^\d+$", f[5]):
                d["rarity"], d["job"] = f[1], f[2]
                d["listName"] = f[3]
                d["cost"] = float(f[4])
                d["lv0Troops"] = int(f[5])
                d["skillLabel"] = f[6]
        m = re.match(r"^初期値 \| (\d+) \| (\d+) \| ([\d.]+)$", ln)
        if m:
            d["atkBase"], d["defBase"] = int(m.group(1)), int(m.group(2))
            d["tacticsBase"] = float(m.group(3))
        m = re.match(r"^成長値 \| \+?([\d.]+) \| \+?([\d.]+) \| \+?([\d.]+)$", ln)
        if m:
            d["atkGrowth"], d["defGrowth"] = float(m.group(1)), float(m.group(2))
            d["tacticsGrowth"] = float(m.group(3))
    # スキル各段(「S 〇〇 LV10」「… TR5 鍛錬」の直後2行が効果)
    eff = []
    for i, ln in enumerate(lines):
        # 2026-08-23: スキル名を (\S+) で取っていたので **名前に空白が入るスキルを
        # 全部取り落としていた**(「真田丸 雁金」「戦姫 陽炎」「戦陣 勝鬨」など)。
        # skillLevels が空になり、その先の /skills/{名前}/ も引けず、
        # initialSkill も合成候補の効果文も null のまま登録されていた。
        # 特の一括登録で12体が空欄のまま出て発覚。空白を許す。
        m = re.match(r"^([A-Z]+) (.+?) (LV\d+|TR\d)( 鍛錬)?$", ln)
        if m:
            eff.append({"rank": m.group(1), "skill": m.group(2), "level": m.group(3),
                        "text": " ".join(lines[i + 1:i + 3])})
    d["skillLevels"] = eff
    # 絵師: 「武将データ」の手前に出る、数字や記号を含まない短い行(紹介文は長いので除く)
    for i, ln in enumerate(lines):
        if ln == "武将データ" and i > 0:
            for j in range(i - 1, max(0, i - 8), -1):
                s = lines[j]
                if 2 <= len(s) <= 12 and not re.search(r"[0-9%：:|、。ワン]", s):
                    d["illustrator"] = s
                    break
            break
    return d


def parse_ixawiki_card(lines):
    d = {}
    for i, ln in enumerate(lines):
        m = re.match(r"^槍 \| (\S+) \| 馬 \| (\S+)$", ln)
        if m:
            d.setdefault("rankGrades", {})["yari"] = m.group(1)
            d["rankGrades"]["uma"] = m.group(2)
        m = re.match(r"^弓 \| (\S+) \| 器 \| (\S+)$", ln)
        if m:
            d.setdefault("rankGrades", {})["yumi"] = m.group(1)
            d["rankGrades"]["ki"] = m.group(2)
        m = re.match(r"^(\S+)\((.+?)\) \| \| Cost \| ([\d.]+) \| 指揮兵数 \| (\d+)$", ln)
        if m:
            d["wikiName"], d["furigana"] = m.group(1), m.group(2)
            d["cost"], d["lv0Troops"] = float(m.group(3)), int(m.group(4))
        if ln.startswith("Illust:"):
            d["illustrator"] = ln[len("Illust:"):].strip()
    return d


LEVEL_LINE = re.compile(r"^(LV\d+|TR\d) \| (.+)$")
# 効果文の続きではない行。ここに当たったら次の行を連結しない。
# 2026-08-15: 以前は次の行を**無条件に**連結していた。効果が1行で終わっている段
# では次の段の行や「合成テーブル」という見出しがそのまま効果文の末尾にくっつき、
# 傾世ノ華では全6段が次の段と連結され、TR5に「合成テーブル」が混入していた。
NOT_CONT = re.compile(r"^(?:LV\d+|TR\d)\s*\||^(?:合成テーブル|合成素材|武将カード"
                      r"|武将スキル|※|・\d次|開発|Home)")


def parse_ixanary_skill(lines):
    d = {"levels": [], "synthesis": {}, "noSynthesis": False}
    for i, ln in enumerate(lines):
        m = LEVEL_LINE.match(ln)
        if m:
            # 2026-08-15: ここは次の**1行だけ**を連結していた。ixanary の効果は
            #   LV10 | 確率：+48% / 対象 全
            #   防御：910%上昇
            #   部隊消費コストを1.5低下
            #   （特殊効果は模倣不可）
            # のように3行以上になることがあり、**2行目以降が丸ごと落ちていた**。
            # 「防御の成分が無い」「模倣不可が無い」「条件が無い」という欠落が
            # 大量に出ていた原因がこれ。次の段か見出しに当たるまで全部拾う。
            cont = []
            j = i + 1
            while j < len(lines) and not NOT_CONT.match(lines[j].strip()):
                if lines[j].strip():
                    cont.append(lines[j].strip())
                j += 1
            d["levels"].append({"level": m.group(1),
                                "text": " / ".join([m.group(2)] + cont).strip()})
        if ln.startswith("1次 | ") or ln.startswith("2次 | "):
            key = "first" if ln.startswith("1次") else "second"
            d["synthesis"][key] = [x.strip() for x in ln.split("|")[1:]]
        if "合成不可スキルです" in ln:
            d["noSynthesis"] = True
    for i, ln in enumerate(lines):
        # 2026-08-23: ここも (\S+) で **空白入りのスキル名を取り落としていた**。
        # 種別の並びも列挙で、攻防速のような組み合わせが増えると漏れる。
        # 「◯◯：スキル名 ランク」の形だけを見るようにした。
        m = re.match(r"^([攻防速破特]{1,4})：(.+?) ([A-Z]+)$", ln)
        if m:
            d["skillName"], d["rank"] = m.group(2), m.group(3)
            break
    return d


def run(no):
    out = {"no": no}
    t = fetch_and_log("busho:%s" % no, "ixanary",
                      "https://ixanary.com/cards/%s/" % no, encoding="utf-8")
    if t:
        out["ixanary"] = parse_ixanary_card(strip(t), no)
    name = (out.get("ixanary") or {}).get("name")
    if name:
        url = ("https://ixawiki.com/index.php?" +
               urllib.parse.quote(("BushoCard/%s%s" % (no, name)).encode("euc_jp"), safe=""))
        t = fetch_and_log("busho:%s" % no, "ixawiki", url, encoding="euc_jp")
        if t:
            out["ixawiki"] = parse_ixawiki_card(strip(t))
    lv = (out.get("ixanary") or {}).get("skillLevels") or []
    sk = lv[0]["skill"] if lv else None
    if sk:
        # 2026-08-23: カードページの本文は空白を半角に潰して読むので、
        # 「忍道　白虎」のように**名前に全角空白が入るスキル**は半角で引いて404になる。
        # 半角で取れなかったら全角に戻して引き直す(No.3211 で発覚)。
        t = fetch_and_log("skill:%s" % sk, "ixanary",
                          "https://ixanary.com/skills/" + urllib.parse.quote(sk),
                          encoding="utf-8")
        if not t and " " in sk:
            sk2 = sk.replace(" ", "　")
            t = fetch_and_log("skill:%s" % sk2, "ixanary",
                              "https://ixanary.com/skills/" + urllib.parse.quote(sk2),
                              encoding="utf-8")
            if t:
                sk = sk2
                for lvv in lv:
                    lvv["skill"] = sk2
        if t:
            out["skill"] = parse_ixanary_skill(strip(t))
    p = os.path.join(HERE, "draft_%s.json" % no)
    io.open(p, "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=1))
    ix, wk = out.get("ixanary", {}), out.get("ixawiki", {})
    print("No.%-6s %-20s cost=%-5s 指揮=%-6s 攻%s(+%s) 防%s(+%s) 兵%s(+%s) 統率=%s 絵師=%s"
          % (no, ix.get("name"), ix.get("cost"), ix.get("lv0Troops"),
             ix.get("atkBase"), ix.get("atkGrowth"), ix.get("defBase"), ix.get("defGrowth"),
             ix.get("tacticsBase"), ix.get("tacticsGrowth"),
             wk.get("rankGrades"), ix.get("illustrator") or wk.get("illustrator")))
    s = out.get("skill") or {}
    print("        スキル %s %s / 段%d / 合成%s"
          % (s.get("skillName"), s.get("rank"), len(s.get("levels") or []),
             "不可" if s.get("noSynthesis") else (s.get("synthesis", {}).get("first") or "取得できず")))
    return out


if __name__ == "__main__":
    for n in sys.argv[1:]:
        run(n)
