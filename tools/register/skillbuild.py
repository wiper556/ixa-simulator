import os as _os
import sys as _sys
# リポジトリの根はこのファイルの位置から求める(決め打ちにするとCIやworktreeで壊れる)
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
HERE = _os.path.join(ROOT, "tools", "register", "_work")
_os.makedirs(HERE, exist_ok=True)
_sys.path.insert(0, _os.path.join(ROOT, "tools", "register"))
# -*- coding: utf-8 -*-
"""ixanaryのスキルページから data/skill/{名前}.json を作る(S-01の新規作成用)。

  python skillbuild.py <スキル名> ...

効果文はixanaryの表記をうちの書式に寄せる:
  「確率：+40% / 対象 全 無尽」+「防御：（8×防御参加武将数）%上昇」
  → target="全 無尽" / baseRate=40 / effect="全 無尽　確率 40% / 防御 …"
確率や対象が読めなかったものはnullのままにして報告する(D-07)。
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
from tools.reslog import fetch_and_log  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from regfetch import strip, parse_ixanary_skill  # noqa: E402

SKILLDIR = os.path.join(ROOT, "data", "skill")
PTS = {"LV10": "-", "TR1": "10", "TR2": "40", "TR3": "90",
       "TR4": "150", "TR5": "200", "TR6": "パラレル"}
Z = str.maketrans("（）％", "()%")


def norm(s):
    return re.sub(r"\s+", " ", s.translate(Z)).strip()


# 対象欄に入りうる語だけを並べたもの。**ここに無い語が出たらそこから先は効果文**。
# 2026-08-15: 以前は「『〜：』か攻撃/防御で始まる語が出たら打ち切り」という
# 除外側の判定だった。対象が「部隊長」の模倣スキルは効果文が
# 「所属部隊の部隊長が持つ模倣可能な初期スキルを、通常攻撃／防御効果を…」で
# 始まり、コロンも攻撃/防御始まりも無いため、**効果文が丸ごと対象欄に流れ込んだ**。
# 1日で8件踏んだので、許す語を並べる側に変えた。
TARGET_TOK = re.compile(
    r"^(?:[全槍弓馬器鉄騎砲焙・]+|部隊長|自身|覇道|追加スキル|極限スキル|兵站|撤退|卓越"
    r"|無尽\d*|不屈\d*|飛翔\d*|-)$")


def parse_level(text):
    """「確率：+40% / 対象 全 無尽 防御：（8×…）%上昇」を分解する。"""
    t = norm(text)
    rate = target = None
    m = re.search(r"確率[：:]\s*\+?([\d.]+)%", t)
    if m:
        rate = float(m.group(1))
    # 「対象 全 無尽 防御：…」のように、対象のあとにキーワード(無尽/撤退/飛翔n)が続く。
    m = re.search(r"対象\s+(.+)$", t)
    if m:
        toks = []
        for tok in m.group(1).split():
            if not TARGET_TOK.match(tok):
                break
            toks.append(tok)
        target = " ".join(toks) or None
    body = t
    if target:
        body = t.split("対象", 1)[1]
        body = body.strip()[len(target):].strip() if body.strip().startswith(target) else body
    body = re.sub(r"^確率[：:]\s*\+?[\d.]+%\s*/?\s*", "", body).strip()
    # うちの書式は「防御 580%上昇」。ixanaryの「防御：580%上昇」から全角コロンを外す
    body = re.sub(r"(攻撃|防御|速度|破壊|総攻撃|総防御)[：:]\s*", r"\1 ", body)
    # 末尾に重複して付いてくる「確率：+26%」を落とす(確率は見出し側に出す)
    body = re.sub(r"\s*確率\s*[：:]\s*\+?[\d.]+%\s*$", "", body).strip()
    return rate, target, body


def build(name, rank=None):
    url = "https://ixanary.com/skills/" + urllib.parse.quote(name)
    raw = fetch_and_log("skill:%s" % name, "ixanary", url, encoding="utf-8")
    if raw is None:
        print("  ★ %-14s 取得できない" % name)
        return None
    d = parse_ixanary_skill(strip(raw))
    lv = [x for x in (d.get("levels") or []) if x["level"] != "LV1"]
    if not lv:
        print("  ★ %-14s 効果の段が読めない" % name)
        return None
    rate0, target0, _ = parse_level(lv[0]["text"])
    tr = []
    for x in lv:
        r, tg, body = parse_level(x["text"])
        head = "%s　確率 %s%%" % (tg or target0 or "全",
                                ("%g" % r) if r is not None else "-")
        tr.append(collections.OrderedDict([
            ("level", x["level"]), ("points", PTS.get(x["level"], "-")),
            ("effect", "%s / %s" % (head, body) if body else head)]))
    entry = collections.OrderedDict([
        ("name", name),
        # ページ側でランクが読めないことがあるので、呼び出し元(武将の合成表の表記)を優先する
        ("rank", rank or d.get("rank")),
        ("baseRate", rate0),
        ("target", target0),
        ("effectSummary", "%s/LV10 確率 %s%% %s/対象:%s"
         % (rank or d.get("rank"), ("%g" % rate0) if rate0 is not None else "-",
            (parse_level(lv[0]["text"])[2] or "").split("(")[0][:40], target0 or "全")),
        ("categoryLinks", []),
        ("sourceCharacters", []),
        ("notes", ["ixanary.com(skills/%s)から登録(2026-08-14)。"
                   "S以上のスキルはページを作る決まり(S-01)にもとづく新規作成。" % name,
                   "対象・確率・効果文はixanaryの表記をうちの書式に直したもの。"
                   "categoryLinksとsourceCharactersは各武将の登録時に追記する。"]),
        ("trTable", tr),
    ])
    p = os.path.join(SKILLDIR, name + ".json")
    io.open(p, "w", encoding="utf-8", newline="\n").write(
        json.dumps(entry, ensure_ascii=False, indent=1) + "\n")
    print("  作成 %-14s %-4s 確率%-6s 対象%-14s 段%d" %
          (name, entry["rank"], entry["baseRate"], entry["target"], len(tr)))
    print("        LV10: %s" % tr[0]["effect"][:100])
    return entry


if __name__ == "__main__":
    # 引数は 名前 か 名前:ランク
    for a in sys.argv[1:]:
        n, _, rk = a.partition(":")
        if os.path.exists(os.path.join(SKILLDIR, n + ".json")) and "--force" not in sys.argv:
            print("  %-14s 既にあり" % n)
            continue
        if n == "--force":
            continue
        build(n, rk or None)
