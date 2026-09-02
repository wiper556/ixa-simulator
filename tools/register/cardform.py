# -*- coding: utf-8 -*-
"""カードから読み取った値を書いた紙から、武将の正本を組み立てる。

なぜ要るか(2026-09-02):
新規登録は、読み取った値を Python の辞書に起こして書き出す手作業だった。
決まりごと(置き場所・画像の道・成長値は null・D-17の鍛錬なし・
合成枠の対応・（N）の要否)は毎回同じなので、道具に任せる。

    python tools/register/cardform.py _work/cards-2026-09-02.txt          下見
    python tools/register/cardform.py _work/cards-2026-09-02.txt --write  書き出す

────────────────────────────────────────────────────────
書き方
────────────────────────────────────────────────────────
    # 行頭が # の行と空行は読み飛ばす。1体につき1ブロック。
    [2640]
    名前: 真田幸村
    読み: さなだゆきむら
    コスト: 5
    対象: 馬
    効果: 攻
    絵師: 真島ヒロ
    攻防兵: 1250 1090 550.0
    指揮: 4830
    統率: 槍A 馬S 弓A 器A
    スキル: 業火ノ六冥破 S 40
    LV10: 対象兵科を指揮した戦闘時に部隊総攻撃力が25%上昇(模倣不可)
    TR5: 対象兵科を指揮した戦闘時に部隊総攻撃力が30%上昇(模倣不可)
    合成: A=紅焔 六冥銭 / B=虎賁統帥 / C=絶界滅刃 / S1=覇天金剛 / S2=星神闘覇
    章: 32章
    覚え書き: 好きなことを書ける(記録に残る)

  ・「効果」は 攻 / 防 / 攻防 / 攻速 のようにカードのスキル欄の色帯に合わせる。
    troop は「対象+効果」で作る(馬+攻 → 馬攻)。
  ・「スキル: 名前 ランク 確率」。確率が無いスキルは確率を省く。
  ・段は LV10 が要る。TR は分かるものだけ書けばよい。
    **間の段は「段はあるが値が不明」として null で埋める**(D-08/V-01)。
  ・「章」は分からなければ書かない。**No.から外挿しない**(register/README)。
  ・「合成」は カードの「スキル追加合成」画面のとおり。
    候補スキル1→A / 2→B / 3→C / 隠し候補→S1 / 同一No合成→S2。
    **移植後は画面に出ないので null のまま。**

道具が自動でやること
  ・置き場所(data/busho*/)を No. から決める(regbuild.kyoku_dir と同じ規則)
  ・imageFull / imageChar の道を入れる(画像が無ければ止める)
  ・**成長値は null**(カードに出ないので。D-07)
  ・D-17: 傑・特・上・序、または28章より前なら LV10 に「(TRなし)」を足す
  ・合成候補の名前を既存スキルと突き合わせ、**知らない名前を挙げる**
    (前回はこれで「先剣ノ直観→先見ノ直観」「白影輪地→白影縮地」の誤読が見つかった)
  ・同じレアリティに同名が居れば知らせる(（N）が要るかもしれない)
"""
import glob
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from regbuild import kyoku_dir            # noqa: E402

NL = chr(10)
TR_PTS = {"TR1": "10", "TR2": "40", "TR3": "90", "TR4": "150",
          "TR5": "200", "TR6": "パラレル"}
ORDER = ["TR1", "TR2", "TR3", "TR4", "TR5", "TR6"]
NO_TANREN = {"busho-ketsu", "busho-toku", "busho-toku-s", "busho-ue", "busho-jo"}
GRADE = ("XXX", "XX", "X", "SSS", "SS", "S", "A", "B", "C", "D", "E", "F")


def load_world():
    cards, skills = {}, set()
    for f in glob.glob(os.path.join(ROOT, "data", "busho*", "*.json")):
        j = json.load(io.open(f, encoding="utf-8"))
        j["_dir"] = os.path.basename(os.path.dirname(f))
        cards[str(j["no"])] = j
        if j.get("initialSkill"):
            skills.add(j["initialSkill"])
        for r in (j.get("synthesisTable") or []):
            for k in ("skill", "afterSkill"):
                if r.get(k):
                    skills.add(r[k])
    for f in glob.glob(os.path.join(ROOT, "data", "skill", "*.json")):
        skills.add(os.path.basename(f)[:-5])
    return cards, skills


def parse(path):
    """紙を読む。返り値は [{No, 項目...}]"""
    out, cur = [], None
    for ln, raw in enumerate(io.open(path, encoding="utf-8").read().split(NL), 1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"^\[(\d+)\]$", s)
        if m:
            cur = {"no": m.group(1), "_line": ln, "段": {}}
            out.append(cur)
            continue
        if cur is None:
            raise SystemExit("%d行目: 先に [No.] を書く: %s" % (ln, s))
        m = re.match(r"^(LV10|TR[1-6])\s*[:：]\s*(.+)$", s)
        if m:
            cur["段"][m.group(1)] = m.group(2).strip()
            continue
        m = re.match(r"^(\S+?)\s*[:：]\s*(.*)$", s)
        if not m:
            raise SystemExit("%d行目: 「項目: 値」の形で書く: %s" % (ln, s))
        cur[m.group(1)] = m.group(2).strip()
    return out


def build(e, cards, skills):
    """1体ぶんの正本を組み立てる。足りなければ SystemExit で止める。"""
    no = e["no"]
    def need(k):
        if k not in e or not e[k]:
            raise SystemExit("No.%s: 「%s」が要る(%d行目のブロック)" % (no, k, e["_line"]))
        return e[k]

    d = kyoku_dir(no)
    path = os.path.join(ROOT, "data", d, no + ".json")
    warn = []
    if os.path.exists(path):
        raise SystemExit("No.%s は既に data/%s/ にある" % (no, d))
    for suf in ("full", "char"):
        p = os.path.join(ROOT, "assets", "img", "characters", "no%s_%s.png" % (no, suf))
        if not os.path.exists(p):
            raise SystemExit("No.%s: 画像 %s が無い。先に crop_card.py を回す"
                             % (no, os.path.basename(p)))

    tgt = need("対象")
    kind = need("効果")
    sk = need("スキル").split()
    if len(sk) < 2:
        raise SystemExit("No.%s: 「スキル: 名前 ランク [確率]」の形で書く" % no)
    sname, srank = sk[0], sk[1]
    if srank not in GRADE:
        raise SystemExit("No.%s: スキルのランク「%s」が読めない" % (no, srank))
    rate = sk[2].rstrip("%") if len(sk) > 2 else None

    ab = need("攻防兵").split()
    if len(ab) != 3:
        raise SystemExit("No.%s: 「攻防兵: 攻 防 兵法」の3つを書く" % no)
    g = {}
    for tok in need("統率").split():
        m = re.match(r"^(槍|弓|馬|器)(.+)$", tok)
        if not m or m.group(2) not in GRADE:
            raise SystemExit("No.%s: 統率「%s」が読めない(例: 槍A)" % (no, tok))
        g[{"槍": "yari", "弓": "yumi", "馬": "uma", "器": "ki"}[m.group(1)]] = m.group(2)
    if len(g) != 4:
        raise SystemExit("No.%s: 統率は槍・弓・馬・器の4つを書く" % no)

    lv = e["段"].get("LV10")
    if not lv:
        raise SystemExit("No.%s: LV10 の段が要る" % no)
    ch = e.get("章") or None
    chn = None
    m = re.match(r"^(\d+)(?:-\d+)?章$", ch or "")
    if m:
        chn = int(m.group(1))
    trs = {k: v for k, v in e["段"].items() if k != "LV10"}
    # D-17: 鍛錬が無いと言い切れるなら LV10 に (TRなし) を足す
    if not trs and (d in NO_TANREN or (chn is not None and chn < 28)):
        why = "レアリティ" if d in NO_TANREN else "%s(28章より前)" % ch
        lv = (lv[:-1] + "・TRなし)") if lv.rstrip().endswith(")") else (lv + "(TRなし)")
        warn.append("D-17により「(TRなし)」を足した(%s)" % why)

    head = tgt + ("　" + " ".join(sk[3:]) if len(sk) > 3 else "")
    rows = [{"level": "LV10", "points": "-",
             "effect": "%s　%s%s" % (head, ("確率 %s%% / " % rate) if rate else "", lv)}]
    if trs:
        top = max(ORDER.index(k) for k in trs)
        for k in ORDER[:top + 1]:
            v = trs.get(k)
            rows.append({"level": k, "points": TR_PTS[k],
                         "effect": ("%s　%s%s" % (head, ("確率 %s%% / " % rate) if rate else "", v))
                                   if v else None})

    # skillDetail の見出しと①②③。**どこで切るかは人が決める**ので紙に書く。
    # 「見出し」を省いたら LV10 の丸括弧より前を使い、①も LV10 のままにする。
    head_txt = e.get("見出し") or re.sub(r"[(（].*$", "", lv).strip()
    bullets = [e[k] for k in ("効果1", "効果2", "効果3", "効果4", "効果5") if e.get(k)]
    if not bullets:
        bullets = [lv]
    detail = ["%s/LV10 %s%s/対象:%s"
              % (srank, ("確率 %s%% " % rate) if rate else "", head_txt, head)]
    detail += ["%s%s" % (mark, b) for mark, b in zip("①②③④⑤", bullets)]

    syn = []
    for tok in re.split(r"\s*/\s*", e.get("合成", "")):
        tok = tok.strip()
        if not tok:
            continue
        m = re.match(r"^(A|B|C|S1|S2)\s*=\s*(.+)$", tok)
        if not m:
            raise SystemExit("No.%s: 合成「%s」が読めない(例: A=虎賁統帥)" % (no, tok))
        syn.append({"slot": m.group(1), "skill": m.group(2).strip(),
                    "rank": None, "afterSkill": None, "afterRank": None})
        if m.group(2).strip() not in skills:
            warn.append("合成候補「%s」は今までに出てこない名前。読み違いでないか確かめる"
                        % m.group(2).strip())

    base = re.sub(r"（\d+）|-復刻[^-]*-|【[^】]*】", "", need("名前")).strip()
    # 極は busho-kyoku と busho-kyoku-ps の2つに分かれているが、レアリティは同じ。
    # 片方だけ見ると（N）の要否を取りこぼす(2026-09-02に気付いた)。
    fam = {"busho-kyoku", "busho-kyoku-ps"} if d.startswith("busho-kyoku") else {d}
    same = [c for c in cards.values()
            if c["_dir"] in fam
            and re.sub(r"（\d+）|-復刻[^-]*-|【[^】]*】", "", c["name"]).strip() == base]
    if same:
        warn.append("同じ枠に同名が %d枚ある(%s)。（N）が要るかもしれない"
                    % (len(same), "・".join("No.%s %s" % (c["no"], c["name"]) for c in same)))

    ent = {
        "name": need("名前"), "no": no, "ch": ch,
        "cost": float(need("コスト")) if "." in need("コスト") else int(need("コスト")),
        "troop": tgt.replace("・", "") + kind, "sub": e.get("職業", ""),
        "effect": e.get("要約") or head_txt,
        "furigana": need("読み"), "illustrator": need("絵師"),
        "imageFull": "assets/img/characters/no%s_full.png" % no,
        "imageChar": "assets/img/characters/no%s_char.png" % no,
        "atkBase": int(ab[0]), "atkGrowth": None,
        "defBase": int(ab[1]), "defGrowth": None,
        "tacticsBase": float(ab[2]), "tacticsGrowth": None,
        "lv0Troops": int(need("指揮")), "rankGrades": g,
        "initialSkill": sname, "skillDetail": NL.join(detail),
    }
    if syn:
        ent["synthesisTable"] = syn
    ent["trTable"] = rows
    ent["reviewedOk"] = False
    note = ("2026-09-02 新規登録。**うぐさん提供のカード画像から採った(D-01)。** "
            "カードに出ない**成長値は null のまま(D-07)**。")
    if not ch:
        note += "**章も分からないので入れていない**(No.から外挿しない)。"
    ent["notes"] = [note]
    if e.get("覚え書き"):
        ent["notes"].append("2026-09-02: " + e["覚え書き"])
    if syn:
        ent["notes"].append(
            "2026-09-02: スキル追加合成の候補はカードの画面から採った"
            "(候補スキル1→A枠 / 2→B枠 / 3→C枠 / 隠し候補→S1枠 / 同一No合成→S2枠)。"
            "**移植後(afterSkill)は画面に出ないので null のまま。**")
    return ent, path, warn


def main(argv):
    if not argv:
        raise SystemExit(__doc__)
    path = argv[0]
    write = "--write" in argv
    cards, skills = load_world()
    made = []
    for e in parse(path):
        ent, dest, warn = build(e, cards, skills)
        made.append((ent, dest, warn))
        print("  No.%-6s %-16s %-14s C%-5s %s [%s] 段=%s"
              % (ent["no"], ent["name"], os.path.basename(os.path.dirname(dest)),
                 ent["cost"], ent["initialSkill"],
                 (ent["skillDetail"].split("/")[0]),
                 "/".join(r["level"] for r in ent["trTable"])))
        for w in warn:
            print("      ※ " + w)
    print()
    if not write:
        print("(下見だけ。--write で書き出す)")
        return 0
    for ent, dest, _w in made:
        io.open(dest, "w", encoding="utf-8", newline=NL).write(
            json.dumps(ent, ensure_ascii=False, indent=1) + NL)
    print("%d体を書き出した。このあと:" % len(made))
    print("  python tools/register/skillbuild.py 名前:S    (S以上のスキルのページ)")
    print("  python tools/register/wireup.py <No.…>        (逆引き・一覧の同期)")
    print("  python tools/build_data.py → prerender.py → gen_detail_pages.py")
    print("  python tools/audit_characters.py")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
