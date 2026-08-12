# -*- coding: utf-8 -*-
"""武将・スキルデータの全件監査ツール(2026-08-07作成)

サイト内のデータ同士の矛盾(内部整合性)と、ixanary.com の一次情報源との
食い違い(外部照合)を、推測を挟まず機械的に洗い出す。

使い方:
    python tools/audit_characters.py            # 内部整合性のみ(オフライン・数秒)
    python tools/audit_characters.py --online   # 外部照合も行う(初回は数分、以降キャッシュ)

結果は tools/audit_out/ に出力される。

■外部照合の原理(最重要・docs/character-registration-manual.md A-3-0 と対応)
ixanary のスキル個別ページ(/skills/{スキル名}/)の合成テーブルは 1次/2次/3次 の世代構造で、
  武将Cのslot Sの skill      = Cの「初期スキル」のページの 1次・slot S
  武将Cのslot Sの afterSkill = 同ページの 2次・slot S
と1対1で対応する。参照先は候補スキル自身のページではなく **武将の初期スキルのページ** である点に注意
(武将のカードページには合成テーブルが載っていない)。

ステータスは /cards/{No}/ の成長表から照合する。ただし ixanary 側にも誤りがあるため
(例: No.1213 のコスト)、不一致が出た場合はカード画像を含む複数ソースで individual に判断すること。
"""
import json, io, re, os, sys, time, collections, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tools", "audit_out")
CACHE_SKILL = os.path.join(OUT, "cache_skill")
CACHE_CARD = os.path.join(OUT, "cache_card")
for d in (OUT, CACHE_SKILL, CACHE_CARD):
    if not os.path.isdir(d):
        os.makedirs(d)
ONLINE = "--online" in sys.argv


# ---------------- データ抽出 ----------------
def extract_array(path, varname):
    """HTML内のJS配列を quickjs で実際に評価して取り出す(正規表現パースより確実)"""
    from quickjs import Context
    with io.open(path, encoding="utf-8") as f:
        html = f.read()
    for s in re.findall(r"<script>([\s\S]*?)</script>", html):
        m = re.search(r"(?:const|let|var)\s+" + re.escape(varname) + r"\s*=\s*", s)
        if not m:
            continue
        start = m.end()
        opener = s[start]
        closer = {"[": "]", "{": "}"}[opener]
        depth, i, in_str, ch_q, in_cmt = 0, start, False, "", False
        while i < len(s):
            c = s[i]
            if in_cmt:
                if c == "\n":
                    in_cmt = False
            elif in_str:
                if c == "\\":
                    i += 2
                    continue
                if c == ch_q:
                    in_str = False
            elif c in "\"'":
                in_str, ch_q = True, c
            elif c == "/" and i + 1 < len(s) and s[i + 1] == "/":
                in_cmt = True
                i += 1
            elif c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    break
            i += 1
        return json.loads(Context().eval("JSON.stringify(" + s[start:i + 1] + ")"))
    raise RuntimeError(varname + " not found in " + path)


def load():
    p = lambda n: os.path.join(ROOT, n)
    return {
        "generals": extract_array(p("characters.html"), "generals"),
        "kyokuGenerals": extract_array(p("characters-kyoku.html"), "kyokuGenerals"),
        "skills": extract_array(p("skills.html"), "skills"),
        "LINKED_SKILLS": extract_array(p("characters.html"), "LINKED_SKILLS"),
        "KK_LINKED_SKILLS": extract_array(p("characters-kyoku.html"), "KK_LINKED_SKILLS"),
    }


def status(g):
    if g.get("approved"):
        return "赤丸"
    if g.get("reviewedOk"):
        return "黄丸"
    if g.get("imageFull"):
        return "青丸"
    return "無印"


def rate_num(r):
    if not r:
        return None
    m = re.findall(r"(\d+(?:\.\d+)?)\s*%", str(r))
    return float(m[0]) if len(m) == 1 else None


def norm(s):
    return re.sub(r"[\s　]", "", s) if s else s


# ---------------- 取得(オンライン時のみ) ----------------
def _fetch(url, path):
    if os.path.exists(path):
        return io.open(path, encoding="utf-8", errors="replace").read()
    if not ONLINE:
        return None
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception as e:
        html = "FETCH_ERROR " + str(e)
    io.open(path, "w", encoding="utf-8").write(html)
    time.sleep(0.7)
    return html


def safe(s):
    return re.sub(r"[^0-9A-Za-z぀-ヿ一-鿿]", "_", s)


def fetch_skill(name):
    return _fetch("https://ixanary.com/skills/" + urllib.parse.quote(name) + "/",
                  os.path.join(CACHE_SKILL, safe(name) + ".html"))


def fetch_card(no):
    return _fetch("https://ixanary.com/cards/" + no + "/",
                  os.path.join(CACHE_CARD, no + ".html"))


def parse_generations(html):
    """合成テーブルの 1次/2次 を {gen: {slot: (skill名, ランク)}} で返す"""
    if not html or html.startswith("FETCH_ERROR") or "Page Not Found" in html:
        return None
    tbl = re.search(r"合成テーブル([\s\S]{0,8000}?)(合成素材|開発)", html)
    if not tbl:
        return None
    res = {}
    for gen in ("1次", "2次"):
        m = re.search(r"<th>" + gen + r"</th>([\s\S]*?)</tr>", tbl.group(1))
        if not m:
            continue
        vals = []
        for c in re.findall(r"<td[^>]*>([\s\S]*?)</td>", m.group(1)):
            t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip()
            if not t:
                vals.append(None)
                continue
            t = re.sub(r"^[^：:]{0,6}[：:]", "", t).strip()
            mr = re.search(r"\s+(SSS|SS|S|A|B|C|D|E|F|X{1,3})$", t)
            vals.append((re.sub(r"\s+(SSS|SS|S|A|B|C|D|E|F|X{1,3})$", "", t).strip(),
                         mr.group(1) if mr else None))
        res[gen] = {s: vals[i] for i, s in enumerate(["A", "B", "C", "S1", "S2"]) if i < len(vals)}
    return res or None


# ---------------- 監査 ----------------
def main():
    D = load()
    skills = {s["name"]: s for s in D["skills"]}
    chars = [(g, "天覇") for g in D["generals"]] + [(g, "極") for g in D["kyokuGenerals"]]
    targets = [(g, s) for g, s in chars if status(g) != "無印"]
    R = []
    add = lambda cat, sev, msg: R.append((cat, sev, msg))

    for n, c in collections.Counter([s["name"] for s in D["skills"]]).items():
        if c > 1:
            add("重複登録", "HIGH", "skills.html に同名スキルが複数: " + n)

    # 移植前/移植後の性能が各スキル自身の登録と食い違わないか
    for g, src in targets:
        st = status(g)
        for row in g.get("synthesisTable") or []:
            sk_, af = row.get("skill"), row.get("afterSkill")
            if sk_ in skills:
                b, rn = skills[sk_].get("baseRate"), rate_num(row.get("rate"))
                if rn is not None and isinstance(b, (int, float)) and abs(rn - b) > 0.01:
                    add("移植前の数値矛盾", "HIGH", "[%s] %s No.%s %s枠: %s の rate=%s / baseRate=%s" %
                        (st, g["name"], g["no"], row.get("slot"), sk_, row.get("rate"), b))
            if af and af in skills and af != sk_:
                a, arn = skills[af].get("baseRate"), rate_num(row.get("afterRate"))
                if row.get("afterRate") is None:
                    add("移植後の性能未設定", "HIGH", "[%s] %s No.%s %s枠: %s→%s の after* が未設定" %
                        (st, g["name"], g["no"], row.get("slot"), sk_, af))
                elif arn is not None and isinstance(a, (int, float)) and abs(arn - a) > 0.01:
                    add("移植後の数値矛盾", "HIGH", "[%s] %s No.%s %s枠: %s の afterRate=%s / baseRate=%s" %
                        (st, g["name"], g["no"], row.get("slot"), af, row.get("afterRate"), a))

    # 同じスキルが武将により別の afterSkill になっていないか
    mapping, where = collections.defaultdict(set), collections.defaultdict(list)
    for g, src in targets:
        for row in g.get("synthesisTable") or []:
            sk_, af = row.get("skill"), row.get("afterSkill")
            if sk_ and af:
                mapping[sk_].add(af)
                where[(sk_, af)].append("%s No.%s %s枠" % (g["name"], g["no"], row.get("slot")))
    for sk_, afs in sorted(mapping.items()):
        if len(afs) > 1:
            add("afterSkill不一致", "HIGH", "「%s」が武将により別スキルに化けている: %s" %
                (sk_, " / ".join("→%s(%s)" % (a, ", ".join(where[(sk_, a)])) for a in sorted(afs))))

    # ownHiddenCandidate は武将側 afterSkill から導出できるはず
    for s in D["skills"]:
        exp = mapping.get(s["name"])
        ohc = (s.get("ownHiddenCandidate") or {}).get("skill")
        if exp and len(exp) == 1:
            e = list(exp)[0]
            if ohc and ohc != e:
                add("ownHiddenCandidate不整合", "HIGH", "「%s」: 登録→%s / 武将側→%s" % (s["name"], ohc, e))
            elif not ohc:
                add("ownHiddenCandidate未設定", "MID", "「%s」: 武将側では→%s" % (s["name"], e))

    # sourceCharacters 逆引き
    for g, src in targets:
        for row in g.get("synthesisTable") or []:
            sk_ = row.get("skill")
            if sk_ in skills and not any(c.get("no") == g["no"] for c in (skills[sk_].get("sourceCharacters") or [])):
                add("逆引き漏れ", "HIGH", "[%s] %s No.%s %s枠の%s が sourceCharacters に無い" %
                    (status(g), g["name"], g["no"], row.get("slot"), sk_))

    # LINKED_SKILLS
    for arr, label, glist in [(D["LINKED_SKILLS"], "LINKED_SKILLS", D["generals"]),
                              (D["KK_LINKED_SKILLS"], "KK_LINKED_SKILLS", D["kyokuGenerals"])]:
        for n in arr:
            if n not in skills:
                add("LINKED_SKILLS", "MID", "%s の「%s」が skills.html に無い" % (label, n))
        used = set()
        for g in glist:
            if status(g) == "無印":
                continue
            for row in g.get("synthesisTable") or []:
                for k in ("skill", "afterSkill"):
                    if row.get(k) in skills:
                        used.add(row[k])
            if g.get("initialSkill") in skills:
                used.add(g["initialSkill"])
        for n in sorted(used - set(arr)):
            add("LINKED_SKILLS", "MID", "%s 未登録だがページ有り: %s" % (label, n))

    # categoryLinks 9項目
    KW = [("無尽", "skills-mujin.html"), ("撤退", "skills-taitai.html"), ("覇道", "skills-hadou.html"),
          ("不屈", "skills-fukutsu.html"), ("兵站", "skills-heitan.html"), ("卓越", "skills-takuetsu.html")]
    for s in D["skills"]:
        text = (s.get("effectSummary") or "") + " " + (s.get("target") or "")
        cl = [c.get("href") for c in (s.get("categoryLinks") or [])]
        for kw, page in KW:
            if kw in text and page not in cl:
                add("categoryLinks漏れ", "MID", "「%s」: 『%s』があるが %s へのリンク無し" % (s["name"], kw, page))
        # 「飛翔を持たない〜」は他者の飛翔の話なので対象外
        if re.search(r"飛翔(?!を持たない)", text) and "飛翔" in (s.get("target") or "") \
                and not any("hishou" in (c or "") for c in cl):
            add("categoryLinks漏れ", "MID", "「%s」: 飛翔一覧へのリンク無し" % s["name"])
        if "防御参加武将数" in text and not any("count" in (c or "") for c in cl):
            add("categoryLinks漏れ", "MID", "「%s」: 人数依存一覧へのリンク無し" % s["name"])

    # 未計算の式
    for g, src in targets:
        for row in g.get("trTable") or []:
            eff = row.get("effect") or ""
            if re.search(r"[×x]\s*(防御参加武将数|無尽武将数|[^\d\s)=]*武将数|人数)", eff) and "=" not in eff:
                add("未計算式", "MID", "[%s] %s No.%s %s: %s" % (status(g), g["name"], g["no"], row.get("level"), eff))

    # 「未確認」と表示される段には、調べた先の記録が要る(RULES.md I-01)
    # 表示規則(RULES.md V-01): 値が判明している一番上の段までは、空でも「未確認」として出る。
    # そこで終わりにするのが2026-08-12の違反(I-01)だったので、
    # tools/research_log.json に2ソース以上の記録が無い「未確認」はHIGHにする。
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    try:
        from reslog import sources as _res_sources
    except Exception:
        _res_sources = lambda k: []
    ORDER = ["LV10", "TR1", "TR2", "TR3", "TR4", "TR5", "TR6"]
    seen_unk = set()
    for g, src in targets + [(s, "skills.html") for s in D["skills"]]:
        rows = g.get("trTable") or []
        filled = [ORDER.index(r["level"]) for r in rows
                  if r.get("effect") and r.get("level", "").startswith("TR")]
        if not filled:
            continue
        blanks = [r["level"] for r in rows
                  if r.get("level", "").startswith("TR") and not r.get("effect")
                  and ORDER.index(r["level"]) < max(filled)]
        if not blanks:
            continue
        skill = g.get("initialSkill") if src != "skills.html" else g.get("name")
        if not skill:
            continue
        key = "TR:" + skill
        got = _res_sources(key)
        if len(got) < 2 and key not in seen_unk:
            seen_unk.add(key)
            add("未確認の根拠なし", "HIGH",
                "「%s」の%sが未確認表示だが、調査ログの情報源が%d件(2件以上必要)。"
                "調べてから埋めるか、当たった先を tools/reslog.py で記録する"
                % (skill, "/".join(blanks), len(got)))

    # ---- 外部照合 ----
    ext = []
    if ONLINE:
        ok = ng = skip = 0
        for g, src in targets:
            init = g.get("initialSkill")
            if not init or not (g.get("synthesisTable") or []):
                continue
            gen = parse_generations(fetch_skill(init))
            if not gen or "1次" not in gen:
                skip += 1
                continue
            bad = []
            for row in g["synthesisTable"]:
                slot = row.get("slot")
                if slot not in ("A", "B", "C", "S1", "S2"):
                    continue
                for key, gname, label in (("skill", "1次", "移植前"), ("afterSkill", "2次", "移植後")):
                    e = (gen.get(gname) or {}).get(slot)
                    if e and e[0] and row.get(key) and norm(e[0]) != norm(row[key]):
                        bad.append("    %s枠 %s: サイト=%s / ixanary%s=%s" % (slot, label, row[key], gname, e[0]))
            if bad:
                ng += 1
                ext.append("[%s] %s No.%s (初期スキル:%s)\n%s" % (status(g), g["name"], g["no"], init, "\n".join(bad)))
            else:
                ok += 1
        ext.insert(0, "合成テーブル照合: 一致%d体 / 不一致%d体 / 照合不可%d体\n" % (ok, ng, skip))

        ok = ng = skip = 0
        stat_bad = []
        for g, src in targets:
            html = fetch_card(g["no"])
            if not html or html.startswith("FETCH_ERROR") or "Page Not Found" in html:
                skip += 1
                continue
            t = re.sub(r"\|+", "|", re.sub(r"<[^>]+>", "|", html))
            m = re.search(r"初期値\|(\d+)\|(\d+)\|([\d.]+)\|成長値\|\+?([\d.]+)\|\+?([\d.]+)\|\+?([\d.]+)\|", t)
            if not m:
                skip += 1
                continue
            exp = {"atkBase": m.group(1), "defBase": m.group(2), "tacticsBase": m.group(3),
                   "atkGrowth": m.group(4), "defGrowth": m.group(5), "tacticsGrowth": m.group(6)}
            m2 = re.search(r"コスト\|指揮\|スキル\|" + re.escape(g["no"]) + r"\|[^|]*\|([\d.]+)\|(\d+)\|", t)
            if m2:
                exp["cost"], exp["lv0Troops"] = m2.group(1), m2.group(2)
            d = []
            for k, v in exp.items():
                got = g.get(k)
                if got is None:
                    continue
                try:
                    if abs(float(got) - float(v)) > 0.001:
                        d.append("    %s: サイト=%s / ixanary=%s" % (k, got, v))
                except Exception:
                    pass
            if d:
                ng += 1
                stat_bad.append("[%s] %s No.%s\n%s" % (status(g), g["name"], g["no"], "\n".join(d)))
            else:
                ok += 1
        ext.append("\nステータス照合: 一致%d体 / 不一致%d体 / 照合不可%d体\n" % (ok, ng, skip))
        ext += stat_bad

    # ---- 出力 ----
    order = {"HIGH": 0, "MID": 1, "LOW": 2}
    R.sort(key=lambda x: (order[x[1]], x[0]))
    with io.open(os.path.join(OUT, "report.txt"), "w", encoding="utf-8") as f:
        f.write("=== 武将・スキルデータ監査 ===\n対象 %d体 / スキル %d件\n\n--- サマリ ---\n"
                % (len(targets), len(D["skills"])))
        for (cat, sev), n in sorted(collections.Counter((c, s) for c, s, m in R).items(),
                                    key=lambda kv: (order[kv[0][1]], -kv[1])):
            f.write("  [%s] %s: %d件\n" % (sev, cat, n))
        f.write("\n内部整合性 合計 %d件\n" % len(R))
        cur = None
        for cat, sev, msg in R:
            if (cat, sev) != cur:
                cur = (cat, sev)
                f.write("\n===== [%s] %s =====\n" % (sev, cat))
            f.write("  " + msg + "\n")
        if ext:
            f.write("\n\n########## 一次情報源(ixanary)との照合 ##########\n")
            f.write("\n".join(ext) + "\n")
        elif not ONLINE:
            f.write("\n(--online を付けると ixanary との外部照合も行う)\n")
    print("wrote " + os.path.join(OUT, "report.txt"))

    # pre-commitフックが差分を取れるよう、機械可読な形でも出す。
    # 本文(msg)をそのままキーにすると件数や武将名の変化で別物になってしまうので、
    # 「種別+深刻度+本文」の組をそのまま指紋として使い、集合の差で新規発生を判定する。
    with io.open(os.path.join(OUT, "findings.json"), "w", encoding="utf-8") as f:
        json.dump([{"cat": c, "sev": s, "msg": m} for c, s, m in R],
                  f, ensure_ascii=False, indent=1)
    print("wrote " + os.path.join(OUT, "findings.json"))


if __name__ == "__main__":
    main()
