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


def violations():
    """違反ログの行。列は | 日付 | ID | 根本原因 | 区分 | 何をしたか | 影響 | 対応 | 監査 |"""
    rows = []
    for line in _read(VIOL).split("\n"):
        if not line.startswith("|"):
            continue
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(c) < 8 or not re.match(r"^\d{4}-\d{2}-\d{2}$", c[0]):
            continue
        rows.append({"date": c[0], "id": c[1], "cause": c[2], "sev": c[3],
                     "what": c[4], "impact": c[5], "fix": c[6], "audit": c[7]})
    return rows


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

    rows = violations()
    if not rows and os.path.exists(VIOL):
        out.append(("違反ログを読めない", "HIGH",
                    "docs/RULE-VIOLATIONS.md から1行も取れない。列構成が変わった可能性"))

    # C-3: 実在しないIDを書けば、そのルールは「初犯」のままにできてしまう
    for r in rows:
        if r["id"] not in ids:
            out.append(("違反ログのIDが索引に無い", "HIGH",
                        "%s の ID「%s」は docs/RULES.md に無い。"
                        "実在するルールIDを使う(無いなら先に索引へ追加する)"
                        % (r["date"], r["id"])))

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
