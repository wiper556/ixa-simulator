# -*- coding: utf-8 -*-
"""ルール索引と違反ログの整合を見る。監査(audit_characters.py)から呼ばれる。

なぜ要るか(C-3/C-4/D-5、2026-08-12レッドチーム指摘):

 C-3 違反ログのIDを書く側(=違反した本人)が選んでいた。
     同じ原因の違反でも別IDを付ければ「2回目」の扱いを避けられる。
     実際 I-01(調べずに未確認と書いた)と S-01(自作の狭い検査で自己合格)は
     どちらも「自分の判定を自分で承認した」という同じ原因なのに別IDになっていて、
     いちばん重いペナルティが発火しなかった。
     → IDは索引に実在するものに限る。加えて根本原因のタグを別列で持ち、
       IDが違ってもタグが同じなら2回目として数える。

 C-4 違反ログは手で書くので、書かなければ何も起きない。
     → 「監査に足したか=まだ」の行を毎回数えて出す。放置が見えるようにする。

 D-5 棚卸しは「月1回」と書いてあるだけで、誰も呼ばないと永久に来ない。
     → 最終棚卸し日と、索引の件数表記が実数と合っているかを毎回見る。
       件数がずれていたら索引が腐り始めた合図。

    python tools/rules.py          # 今の状態を見る
"""
import collections
import datetime
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(ROOT, "docs", "RULES.md")
VIOL = os.path.join(ROOT, "docs", "RULE-VIOLATIONS.md")
OPER = os.path.join(ROOT, "docs", "RULE-OPERATION.md")
STALE_DAYS = 31


def _read(p):
    if not os.path.exists(p):
        return ""
    with io.open(p, encoding="utf-8") as f:
        return f.read()


def rule_ids():
    """索引の本体テーブルにあるID。末尾のまとめ表(「D-01 / D-02」のような
    複数IDをまとめた行)は拾わないよう、1セル1IDの行だけを見る。"""
    out = {}
    for line in _read(RULES).split("\n"):
        m = re.match(r"^\|\s*([A-Z]-\d{2})\s*\|(.*)$", line)
        if m:
            out.setdefault(m.group(1), m.group(2).split("|")[0].strip())
    return out


SEV = ("軽", "中", "重")


def _table_lines(text, header_key):
    """指定の見出しを持つ表の、データ行だけを返す。

    E-8 対応: 以前は「| で始まり8列で日付形式に一致する行」だけを拾い、
    条件を満たさない行は**黙って捨てて**いた。列を1つ減らす・日付を `2026/08/13` にする・
    行頭に空白を1つ入れる、のどれかで違反行を検査から消せた(実証済み)。
    表の範囲を先に決めてから、その中の行は全部「解釈できたか」を見る。
    """
    out, inside = [], False
    for raw in text.split("\n"):
        line = raw.rstrip()
        s = line.strip()
        if not s.startswith("|"):
            if inside and s == "":
                inside = False
            continue
        cells = [x.strip() for x in s.strip("|").split("|")]
        if not inside:
            if header_key in cells:
                inside = True
            continue
        if set("".join(cells)) <= set("-: "):     # 区切り行
            continue
        out.append((raw, cells))
    return out


def violations():
    """違反ログの行。列は | 日付 | ID | 根本原因 | 区分 | 何をしたか | 影響 | 対応 | 監査 |

    解釈できない行は捨てずに parse_error として返す。捨てると隠せてしまうため。
    """
    rows = []
    for raw, c in _table_lines(_read(VIOL), "根本原因"):
        if len(c) != 8 or not re.match(r"^\d{4}-\d{2}-\d{2}$", c[0]):
            rows.append({"parse_error": raw.strip()[:110], "date": "", "id": "",
                         "cause": "", "sev": "", "what": "", "impact": "",
                         "fix": "", "audit": ""})
            continue
        rows.append({"parse_error": None, "date": c[0], "id": c[1], "cause": c[2],
                     "sev": c[3], "what": c[4], "impact": c[5], "fix": c[6],
                     "audit": c[7]})
    return rows


def cause_tags():
    """ログ末尾のタグ表に載っている根本原因タグ。ここに無いタグは使えない。"""
    return {c[0] for _raw, c in _table_lines(_read(VIOL), "タグ") if len(c) >= 2}


def known_checks():
    """監査が実際に出しうるチェック種別の名前。

    「監査に足した」と書いたときに、その種別が本当に存在するかを照合するために使う。
    """
    names = set(re.findall(r'add\("([^"]+)"', _read(os.path.join(ROOT, "tools",
                                                                 "audit_characters.py"))))
    names |= set(re.findall(r'out\.append\(\("([^"]+)"',
                            _read(os.path.join(ROOT, "tools", "rules.py"))))
    # 監査ではなく pre-commit / PreToolUse で止めているものも「機械で見ている」に含める
    names |= {"pre-commit", "pre-push", "pre-merge-commit", "PreToolUse",
              "audit_selftest", "check_js", "install_hooks"}
    return names


def last_inventory():
    m = re.search(r"最終棚卸し:\s*(\d{4}-\d{2}-\d{2})", _read(RULES))
    return m.group(1) if m else None


def problems():
    """(種別, 深刻度, 本文) のリスト。監査がそのまま指摘として出す。"""
    out = []
    ids = rule_ids()
    n = len(ids)

    # D-5: 索引の件数表記が実数とずれていないか。ずれ=棚卸しがされていない合図。
    for f in ("docs/RULES.md", "docs/RULE-OPERATION.md",
              ".claude/agents/data-writer.md", ".claude/agents/kanshi-yaku.md"):
        for m in re.finditer(r"(\d+)\s*件の索引", _read(os.path.join(ROOT, f))):
            if int(m.group(1)) != n:
                out.append(("ルール件数の表記ずれ", "MID",
                            "%s が「%s件の索引」と書いているが実数は%d件。"
                            "棚卸し(RULE-OPERATION.md)をしていない合図"
                            % (f, m.group(1), n)))

    # D-5: 棚卸しの期限
    d = last_inventory()
    if not d:
        out.append(("棚卸しの記録が無い", "MID",
                    "docs/RULES.md に「最終棚卸し: YYYY-MM-DD」の行が無い。"
                    "期限を機械で見られない"))
    else:
        age = (datetime.date.today() - datetime.date.fromisoformat(d)).days
        if age > STALE_DAYS:
            out.append(("棚卸しの期限切れ", "MID",
                        "最終棚卸しが %s(%d日前)。%d日を超えた。"
                        "RULE-OPERATION.md「定期的な棚卸し」を実施する" % (d, age, STALE_DAYS)))

    all_rows = violations()
    if not all_rows and os.path.exists(VIOL):
        out.append(("違反ログを読めない", "HIGH",
                    "docs/RULE-VIOLATIONS.md から1行も取れない。列構成が変わった可能性"))

    # E-8: 解釈できない行を黙って捨てない。捨てれば書式を崩すだけで隠せる。
    for r in all_rows:
        if r["parse_error"]:
            out.append(("違反ログの行を解釈できない", "HIGH",
                        "8列 + 日付YYYY-MM-DD の形になっていない: 「%s」。"
                        "崩れた行は検査から外れるので、書式を直す" % r["parse_error"]))
    rows = [r for r in all_rows if not r["parse_error"]]

    # C-3: 実在しないIDを書けば、そのルールは「初犯」のままにできてしまう
    tags = cause_tags()
    checks = known_checks()
    for r in rows:
        if r["id"] not in ids:
            out.append(("違反ログのIDが索引に無い", "HIGH",
                        "%s の ID「%s」は docs/RULES.md に無い。"
                        "実在するルールIDを使う(無いなら先に索引へ追加する)"
                        % (r["date"], r["id"])))
        # E-9: 区分は3語のみ。「中程度」等と書けば未対応の集計から外れてしまう。
        if r["sev"] not in SEV:
            out.append(("違反ログの区分が不正", "HIGH",
                        "%s %s の区分「%s」は 軽/中/重 のいずれでもない"
                        % (r["date"], r["id"], r["sev"])))
        # E-9: タグを毎回新造すれば、根本原因での2回目判定が永久に発火しない。
        if r["cause"] and r["cause"] not in tags:
            out.append(("違反ログのタグが表に無い", "HIGH",
                        "%s %s のタグ「%s」は末尾のタグ表に無い。"
                        "その場限りのタグを作らない(2回目判定が無効になる)"
                        % (r["date"], r["id"], r["cause"])))
        # E-9: 「足した」と書くだけで停止が解除できた。種別名を要求し、実在を照合する。
        a = r["audit"]
        if a.startswith("足した"):
            m = re.match(r"^足した\((.+)\)$", a)
            if not m:
                out.append(("監査に足した根拠が書式外", "HIGH",
                            "%s %s: 「足した(チェック種別)」の形で書く。今は「%s」"
                            % (r["date"], r["id"], a[:40])))
            else:
                # 「/」ではパス名が割れるので区切りに使わない
                named = [x.strip() for x in re.split(r"[+、,]", m.group(1))]
                unknown = [x for x in named
                           if not any(k in x or x in k for k in checks)]
                if unknown:
                    out.append(("監査に足したチェックが実在しない", "HIGH",
                                "%s %s: 「%s」に対応するチェックが見つからない。"
                                "audit_characters.py / rules.py の種別名か、"
                                "pre-commit / pre-push / PreToolUse / check_js 等を書く"
                                % (r["date"], r["id"], "/".join(unknown)[:60])))

    # C-3: 2回目の判定。IDだけでなく根本原因のタグでも数える。
    for label, key in (("ルールID", "id"), ("根本原因", "cause")):
        for k, c in collections.Counter(r[key] for r in rows if r[key] and r[key] != "-").items():
            if c < 2:
                continue
            # 「不可(層3)」を済み扱いにすると、いちばん繰り返す種類ほど
            # 「機械化できません」と書くだけで2回目の停止を免れてしまう。
            # 2回起きた時点で「記憶では守れない」と確定した以上、
            # 部分的にでも機械で見る手段を作るまでは済みにしない。
            done = all(r["audit"].startswith("足した") for r in rows if r[key] == k)
            if not done:
                out.append(("2回目の違反で作業停止中", "HIGH",
                            "%s「%s」が%d回目。監査にチェックを足すまで該当作業を再開しない"
                            "(RULE-OPERATION.md「同じルールを2回破ったとき」)。"
                            "全部は機械化できなくても、部分的に見られる形にしてから再開する"
                            % (label, k, c)))

    # C-4: 未対応の宿題が見えないまま溜まるのを防ぐ
    open_ = [r for r in rows if r["sev"] in ("中", "重") and r["audit"] in ("まだ", "未", "")]
    if open_:
        out.append(("違反ログに未対応が残っている", "MID",
                    "区分が中/重で監査へ未反映の行が%d件(%s)。"
                    "落とせないなら「不可(層3)」と書いて理由を残す"
                    % (len(open_), "/".join(r["id"] for r in open_))))
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ids = rule_ids()
    print("ルール: %d件 %s" % (len(ids), dict(collections.Counter(k[0] for k in ids))))
    print("最終棚卸し: %s" % (last_inventory() or "記録なし"))
    rows = violations()
    print("違反ログ: %d件" % len(rows))
    for r in rows:
        print("  %s %-5s %-3s %-22s 監査=%s" % (r["date"], r["id"], r["sev"],
                                                r["cause"][:22], r["audit"]))
    p = problems()
    print("\n指摘 %d件" % len(p))
    for cat, sev, msg in p:
        print("  [%s] %s: %s" % (sev, cat, msg))
