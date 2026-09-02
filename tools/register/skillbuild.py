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
import datetime
TODAY = datetime.date.today().isoformat()
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
# 2026-08-23: 兵科と一部のキーワードしか対象と認めていなかったので、
# **職業を対象にするスキル(将/姫)と、スラッシュ区切りの対象(弓/砲、弓/馬/器)を
# 全部取り落としていた。** 対象が "全" に落ち、本文の先頭に「対象 将 /」が
# 残ったまま登録される。特の一括登録で19体に出て発覚した。
TARGET_TOK = re.compile(
    r"^(?:[全槍弓馬器鉄騎砲焙・/]*[全槍弓馬器鉄騎砲焙][全槍弓馬器鉄騎砲焙・/]*"
    r"|部隊長|自身|覇道|追加スキル|極限スキル|兵站|撤退|卓越"
    r"|将|姫|合流|秘境兵|無尽\d*|不屈\d*|飛翔\d*|-)$")


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
    # 効果が複数行にわたるスキルは行を「 / 」でつないでいるので、対象を取り除いた
    # 残りが「/ 攻撃：22%上昇 / 防御：22%上昇」のように区切りで始まることがある
    body = body.lstrip("/ ").strip()
    # うちの書式は「防御 580%上昇」。ixanaryの「防御：580%上昇」から全角コロンを外す
    body = re.sub(r"(攻撃|防御|速度|破壊|総攻撃|総防御)[：:]\s*", r"\1 ", body)
    # 重複して付いてくる「確率：+26%」を落とす(確率は見出し側に出す)。
    # 2026-09-02: **末尾だけを見ていたので、途中に出るものが残っていた。**
    # 不滅ノ鬼美濃で「攻撃 100%上昇 確率：+70% / 速度 70%上昇」となっていた。
    body = re.sub(r"\s*確率\s*[：:]\s*\+?[\d.]+%\s*", " ", body)
    body = re.sub(r"\s*/\s*/\s*", " / ", body)       # 落とした跡の空区切りを畳む
    body = re.sub(r"\s{2,}", " ", body).strip(" /").strip()
    return rate, target, body


def headline(body):
    """effectSummary の見出しに出す短い形。

    2026-09-02: もとは body.split("(")[0][:40] だった。**効果文が括弧で
    始まるスキルでは中身が丸ごと消えていた**(列侯擁媛が「防御 」だけになった)。
    括弧の前に中身があるときだけ切り、無ければそのまま使う。
    """
    head = body.split("(")[0].strip()
    # 括弧の前に数値が無ければ、そこで切っても意味が残らない
    # (列侯擁媛の「防御 (敵部隊移動速度÷0.7)%上昇」が「防御」だけになった)
    if not head or not re.search(r"[\d.]", head):
        head = body
    if len(head) > 60:
        cut = head.rfind(" / ", 0, 60)
        head = head[:cut] if cut > 20 else head[:60]
    return head.strip()


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
        # 2026-08-23: 段の名前を "LV10" と決め打ちしていた。童のスキルは
        # レベルを持たず「固定」の1段だけなので、実際の段名を使う
        # (regwrite.py にも同じ決め打ちがあり、そちらは同日に直した)。
        # 2026-08-26: rank が None のとき文字列に "None" が出ていた
        # (肥後の虎・表裏比興)。skillDetail と同じく "-" と書く。
        # 末尾が空の効果文だと " / /" が残るので畳む。
        ("effectSummary", ("%s/%s 確率 %s%% %s/対象:%s"
                           % (rank or d.get("rank") or "-", lv[0].get("level") or "LV10",
            ("%g" % rate0) if rate0 is not None else "-",
            headline(parse_level(lv[0]["text"])[2] or ""),
            target0 or "全")).replace(" / /", " /")),
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


def build_from_card(name, rank=None):
    """ixanary にまだ載っていないスキルを、持ち主のカードの正本から組む。

    2026-09-02: 新しいカードのスキルは ixanary が404を返すので、
    こちらしか道が無い(No.7023〜7026 の4件で必要になった)。
    値はカードから写すだけで、新しく計算する数字は無い。
    """
    import glob as _g
    import collections as _c
    owner = None
    for f in _g.glob(os.path.join(ROOT, "data", "busho*", "*.json")):
        j = json.load(io.open(f, encoding="utf-8"))
        if j.get("initialSkill") == name:
            owner = (j, os.path.basename(os.path.dirname(f)))
            break
    if owner is None:
        print("  ★ %-14s 初期スキルとして持つ武将が居ないのでカードから組めない" % name)
        return None
    c, d = owner
    slots = _c.defaultdict(list)
    dbof = {}
    for f in _g.glob(os.path.join(ROOT, "data", "busho*", "*.json")):
        j = json.load(io.open(f, encoding="utf-8"))
        dd = os.path.basename(os.path.dirname(f))
        dbof[j["no"]] = ("kyoku" if dd in ("busho-kyoku", "busho-kyoku-ps")
                         else "toku" if dd == "busho-toku-s"
                         else "ketsu" if dd == "busho-ketsu" else None)
        for r in (j.get("synthesisTable") or []):
            if r.get("skill") == name:
                slots[j["no"]].append(r.get("slot"))
        if j.get("initialSkill") == name and j["no"] not in slots:
            slots[j["no"]] = ["移植不可"]
    src = []
    for no in sorted(slots, key=lambda x: (len(x), x)):
        j = json.load(io.open(_g.glob(os.path.join(ROOT, "data", "busho*", no + ".json"))[0],
                              encoding="utf-8"))
        e = collections.OrderedDict([("name", j["name"]), ("no", no),
                                     ("slot", "・".join(dict.fromkeys(slots[no])))])
        if dbof.get(no):
            e["db"] = dbof[no]
        if e["slot"] == "移植不可":
            e["note"] = ["%s(%s)の初期スキル。合成候補には出てこない(%s)" % (j["name"], no, TODAY)]
        src.append(e)
    sd = c.get("skillDetail") or ""
    head = sd.split("/")[0].strip()
    m = re.search(r"確率\s*([\d.]+)\s*%", sd)
    tgt = re.search(r"対象:(\S+)", sd)
    ent = collections.OrderedDict()
    ent["name"] = name
    ent["rank"] = rank or (head if head in ("SSS", "SS", "S", "A", "B", "C", "D", "E", "F") else None)
    if m:
        ent["baseRate"] = float(m.group(1))
    ent["target"] = tgt.group(1) if tgt else "全"
    ent["effectSummary"] = sd
    if "模倣不可" in sd:
        ent["noMimic"] = True
    ent["categoryLinks"] = []
    ent["sourceCharacters"] = src
    ent["grantedViaSkills"] = []
    ent["trTable"] = [dict(r) for r in (c.get("trTable") or [])]
    ent["notes"] = ["%s: **ixanary にこのスキルのページがまだ無い(404)**ため、"
                    "持ち主 No.%s(%s)の正本から組んだ。値はカードから写しただけで、"
                    "新しく計算した数字は無い。" % (TODAY, c["no"], c["name"])]
    io.open(os.path.join(SKILLDIR, name + ".json"), "w", encoding="utf-8",
            newline=chr(10)).write(json.dumps(ent, ensure_ascii=False, indent=1) + chr(10))
    print("  作成 %-14s %-4s 確率%-6s 対象%-14s カードから(No.%s)"
          % (name, ent.get("rank"), ent.get("baseRate"), ent["target"], c["no"]))
    return ent


if __name__ == "__main__":
    # 引数は 名前 か 名前:ランク。--from-card を付けると ixanary ではなく
    # 持ち主のカードの正本から組む(新カードで ixanary が404のとき)
    from_card = "--from-card" in sys.argv
    for a in sys.argv[1:]:
        n, _, rk = a.partition(":")
        if os.path.exists(os.path.join(SKILLDIR, n + ".json")) and "--force" not in sys.argv:
            print("  %-14s 既にあり" % n)
            continue
        if n in ("--force", "--from-card"):
            continue
        (build_from_card if from_card else build)(n, rk or None)
