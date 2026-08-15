import os as _os
import sys as _sys
# リポジトリの根はこのファイルの位置から求める(決め打ちにするとCIやworktreeで壊れる)
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
HERE = _os.path.join(ROOT, "tools", "register", "_work")
_os.makedirs(HERE, exist_ok=True)
_sys.path.insert(0, _os.path.join(ROOT, "tools", "register"))
# -*- coding: utf-8 -*-
"""今回足した武将まわりの配線をまとめて直す。

  S-05 合成候補スキルの sourceCharacters に武将を足す(逆引き)
  S-08 新しく作ったスキルページの ownHiddenCandidate を、そのスキルが
       1次候補として載っている枠の2次(武将側の afterSkill)から決める
  S-04 KP_LINKED_SKILLS に足す
  S-06 一覧ページ(skills-*.html)側の sourceCharacters を正本に合わせる
"""
import collections
import datetime
import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")


sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

NOS = sys.argv[1:]
SKILLDIR = os.path.join(ROOT, "data", "skill")
TODAY = datetime.date.today().isoformat()
# 正本の置き場所 → sourceCharacters の db。極は2ページに分かれたが、
# どちらも #No で busho/{No}.html へ転送されるので db は "kyoku" のまま(S-07)。
DB_OF_DIR = collections.OrderedDict([
    ("busho-kyoku", "kyoku"), ("busho-kyoku-ps", "kyoku"),
    ("busho-ketsu", "ketsu"), ("busho", None)])


def find_general(no):
    """正本のどこに居るかを探して (中身, db) を返す。"""
    for d, db in DB_OF_DIR.items():
        fp = os.path.join(ROOT, "data", d, "%s.json" % no)
        if os.path.exists(fp):
            return json.load(io.open(fp, encoding="utf-8")), db
    return None, None


# 章を引くための表。sourceCharacters の並べ替えに使う。
CH_OF_NO = {}
for _d in DB_OF_DIR:
    _p = os.path.join(ROOT, "data", _d)
    if os.path.isdir(_p):
        for _fn in os.listdir(_p):
            if _fn.endswith(".json"):
                CH_OF_NO[_fn[:-5]] = json.load(
                    io.open(os.path.join(_p, _fn), encoding="utf-8")).get("ch")


def src_key(c):
    """入手可能な武将の並び: 章の昇順 → カードNo.の昇順。未確認は最後。"""
    no = str(c.get("no") or "")
    m = re.match(r"^(\d+)章$", CH_OF_NO.get(no) or "")
    return (int(m.group(1)) if m else 9999, int(no) if no.isdigit() else 0)

# --- S-05: 逆引き ---
added = 0
via = 0
for no in NOS:
    ent, db = find_general(no)
    if ent is None:
        print("  ★ No.%s が正本に無い" % no)
        continue
    for r in ent.get("synthesisTable") or []:   # 傑には合成表が無い
        # 2026-08-15: ここは skill と afterSkill の**両方**に武将を足していた。
        # A-3-12 は「skill != afterSkill のとき、武将を afterSkill 側に直接
        # 列挙してはいけない。afterSkill 側には grantedViaSkills で元スキルを
        # 1回だけ書く」と定めており、**このスクリプトが規則に反していた。**
        # 監査の逆引き検査も skill 側しか見ていない(A-3-12と一致)。
        # 黄丸化の検証で担当が直した分を、こちらが何度も上書きしてしまっていた。
        nm = r.get("skill")
        if nm:
            sp = os.path.join(SKILLDIR, nm + ".json")
            if os.path.exists(sp):
                js = json.load(io.open(sp, encoding="utf-8"),
                               object_pairs_hook=collections.OrderedDict)
                sc = js.setdefault("sourceCharacters", [])
                if not any(str(x.get("no")) == no for x in sc):
                    row = collections.OrderedDict([
                        ("name", ent["name"]), ("no", no), ("slot", r["slot"])])
                    if db:
                        row["db"] = db
                    row["note"] = ["%s(%s)のsynthesisTable %s枠(%s)"
                                   % (ent["name"], no, r["slot"], TODAY)]
                    sc.append(row)
                    # 追記した順のままだと章もカード番号もばらばらになる
                    js["sourceCharacters"] = sorted(sc, key=src_key)
                    io.open(sp, "w", encoding="utf-8", newline="\n").write(
                        json.dumps(js, ensure_ascii=False, indent=1) + "\n")
                    added += 1

        # 別スキル経由で得られる枠は、移植後スキル側に grantedViaSkills を1回だけ
        af = r.get("afterSkill")
        if af and nm and af != nm and os.path.exists(os.path.join(SKILLDIR, nm + ".json")):
            ap = os.path.join(SKILLDIR, af + ".json")
            if os.path.exists(ap):
                js = json.load(io.open(ap, encoding="utf-8"),
                               object_pairs_hook=collections.OrderedDict)
                gv = js.setdefault("grantedViaSkills", [])
                if not any(x.get("skill") == nm for x in gv):
                    gv.append(collections.OrderedDict([
                        ("skill", nm), ("rank", r.get("rank"))]))
                    io.open(ap, "w", encoding="utf-8", newline="\n").write(
                        json.dumps(js, ensure_ascii=False, indent=1) + "\n")
                    via += 1
print("S-05 逆引きを %d件 / grantedViaSkills を %d件 追記" % (added, via))

# --- S-08: 武将側の afterSkill から ownHiddenCandidate を決める ---
for no in NOS:
    ent, _ = find_general(no)
    if ent is None:
        continue
    for r in ent.get("synthesisTable") or []:   # 傑には合成表が無い
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

# --- S-04: 各一覧ページの LINKED_SKILLS ---
# 2026-08-15: ここは characters-kyoku-ps.html(KP_)しか見ていなかった。
# 極(通常)や天の武将にスキルページを新設しても KK_/無印の配列に入らず、
# 監査の「LINKED_SKILLS 未登録だがページ有り」が消えないままだった。
# 武将がどのDBに居るかで書き込む配列を選ぶ。
LINKED_OF_DIR = {
    "busho-kyoku-ps": ("characters-kyoku-ps.html", "KP_LINKED_SKILLS"),
    "busho-kyoku": ("characters-kyoku.html", "KK_LINKED_SKILLS"),
    "busho": ("characters.html", "LINKED_SKILLS"),
}
want = collections.defaultdict(list)
for no in NOS:
    for d, (page, var) in LINKED_OF_DIR.items():
        fp = os.path.join(ROOT, "data", d, "%s.json" % no)
        if not os.path.exists(fp):
            continue
        e = json.load(io.open(fp, encoding="utf-8"))
        # ページがあるスキルだけを入れる。A以下でページを作らないものを入れると
        # 監査が「LINKED_SKILLSにあるのにskills.htmlに無い」と鳴る(S-02)
        if e.get("initialSkill") and os.path.exists(
                os.path.join(SKILLDIR, e["initialSkill"] + ".json")):
            want[(page, var)].append(e["initialSkill"])
        for r in e.get("synthesisTable") or []:
            for k in ("skill", "afterSkill"):
                if r.get(k) and os.path.exists(os.path.join(SKILLDIR, r[k] + ".json")):
                    want[(page, var)].append(r[k])
        break

for (page, var), names in sorted(want.items()):
    p = os.path.join(ROOT, page)
    t = io.open(p, encoding="utf-8", newline="").read()
    m = re.search(r"(const %s = \[)(.*?)(\];)" % var, t, re.S)
    if not m:
        print("S-04 %-24s の %s が見つからない" % (page, var))
        continue
    body = m.group(2).rstrip()
    add = [n for n in dict.fromkeys(names) if n and ("'%s'" % n) not in body]
    if add:
        body += "".join(", '%s'" % n for n in add)
        io.open(p, "w", encoding="utf-8", newline="").write(
            t[:m.start()] + m.group(1) + body + m.group(3) + t[m.end():])
    print("S-04 %-20s へ %d件 追加: %s" % (var, len(add), " ".join(add)))


# --- S-06: 一覧ページ(skills-*.html)の sourceCharacters を正本に合わせる ---
# 一覧ページは skills.html と違って生成物ではなく、sourceCharacters を独自に
# 複製している。ここだけ手で足すのを忘れると監査の「一覧の逆引き同期漏れ」で鳴る。
# 引数の武将に限らず**全ページを正本と突き合わせる**(取りこぼしを溜めないため)。

def _close_bracket(s, i):
    """s[i] == '[' のとき、対応する ']' の位置を返す。"""
    depth = 0
    while i < len(s):
        if s[i] == "[":
            depth += 1
        elif s[i] == "]":
            depth -= 1
            if depth == 0:
                return i
        elif s[i] == '"':                       # 文字列の中の括弧は数えない
            i = s.index('"', i + 1)
        i += 1
    raise ValueError("閉じ括弧が見つからない")


def sync_list_pages():
    master = {}
    for n in os.listdir(SKILLDIR):
        if n.endswith(".json"):
            js = json.load(io.open(os.path.join(SKILLDIR, n), encoding="utf-8"))
            master[n[:-5]] = js.get("sourceCharacters") or []
    total = 0
    for page in sorted(os.listdir(ROOT)):
        if not (page.startswith("skills-") and page.endswith(".html")):
            continue
        fp = os.path.join(ROOT, page)
        text = io.open(fp, encoding="utf-8", newline="").read()
        hits = []
        for m in re.finditer(r'\{name:"([^"]+)", skillPage:"', text):
            nm = m.group(1)
            if nm not in master:
                continue
            k = text.find("sourceCharacters:[", m.end())
            if k == -1:
                continue
            open_at = k + len("sourceCharacters:")
            close_at = _close_bracket(text, open_at)
            inner = text[open_at + 1:close_at]
            listed = set(re.findall(r'no:"(\d+)"', inner))
            missing = [c for c in master[nm] if str(c.get("no")) not in listed]
            if missing:
                hits.append((close_at, inner, nm, missing))
        # 後ろから差し込む(前を先に触ると位置がずれる)
        for close_at, inner, nm, missing in reversed(hits):
            ind = re.search(r"\n(\s*)\{name:", inner)
            ind = ind.group(1) if ind else "        "
            rows = []
            for c in missing:
                r = '%s{name:"%s", no:"%s", slot:"%s"' % (
                    ind, c.get("name"), c.get("no"), c.get("slot"))
                if c.get("db"):
                    r += ', db:"%s"' % c["db"]
                rows.append(r + "}")
                print("  S-06 %-22s %-14s ← No.%s(%s枠)"
                      % (page, nm, c.get("no"), c.get("slot")))
                total += 1
            head = inner.rstrip()
            joined = (head + ",\n" if head else "\n") + ",\n".join(rows) + "\n" + ind[:-2]
            text = text[:close_at - len(inner)] + joined + text[close_at:]
        if hits:
            io.open(fp, "w", encoding="utf-8", newline="").write(text)
    print("S-06 一覧ページへ %d件 追記" % total)


sync_list_pages()
