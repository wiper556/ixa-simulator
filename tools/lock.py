# -*- coding: utf-8 -*-
"""「あるはずのもの」を外に固定する錠前。

なぜ要るか(2026-08-13 第3回レッドチーム、4体が同じ結論に達した):

  検査する側が、検査される側と同じ可変物を素材にしていた。

第2回で「検査する側と比べる側を同じツリーから取る」に揃えた結果、
**ツリーを書ける者が比較基準も書ける**状態になった。実証された抜け道:

 ・監査のチェックを丸ごと消す      → 自己テストは分母からも消えるので緑のまま(G-8/I-5)
 ・監査の条件だけ緩める(呼び出しは残す) → 何も鳴らない(H-3)
 ・直せないHIGHは武将ごと消す      → 集合の差しか見ていないので通る(H-7)
 ・索引からルール行を消す          → 件数表記を直せば無検査(I-8)
 ・違反ログの行を消す              → 何も鳴らない(F-4)
 ・タグ表に1行足してから使う        → 2回目判定が外れる(F-5/H-10)
 ・フックの正本に1行足す           → 一致検査は「正本と同じか」しか見ない(G-7)
 ・ワークフロー/フック設定を消す     → どの検査の対象でもない(F-8/H-9/G-9)

共通するのは「**減った・すり替わった**ことを誰も見ていない」こと。
増えるのは自由、**減るのは要申告**にするための一覧をここに置く。

    python tools/lock.py            # 今の状態と錠前を突き合わせる
    python tools/lock.py --update   # 錠前を今の状態に合わせる(増加を取り込む)

`--update` で減らすときは理由が要る(`--reason`)。理由は tools/lock_reason.txt に残る。
"""
import datetime
import hashlib
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "checks.lock")
REASON = os.path.join(ROOT, "tools", "lock_reason.txt")

# 消えたら困るファイル。中身のハッシュまで見るもの(門番そのもの)は HASHED に置く。
#
# 2026-08-13 第4回レッドチーム: HASHED がフック4本だけだったため、
#  P-1 CIの各ステップに `|| true` を足す → 全部緑のまま無言化
#  P-4/M-2 settings.json の PreToolUse を PostToolUse に改名 → T-01が死ぬが指摘0件
#  P-3 rollback-floor.txt を単独コミットで自分の足元まで進める
#  J-2b checks.lock 自体を手で1行消す
#  K-3/M-1 precommit_check.py / audit_characters.py を書き換えて固有の門番を消す
#  Q-5 RULE-OPERATION.md の区分表を書き換えて全部を軽にする
# がいずれも通った。「門を動かすもの」は中身ごと固定する。
HASHED = ("tools/hooks/pre-commit", "tools/hooks/pre-merge-commit",
          "tools/hooks/pre-push", "tools/hooks/no_heredoc_backslash.py",
          "tools/hooks/no_protection_bypass.py", "docs/branch-protection.json",
          "tools/precommit_check.py", "tools/audit_characters.py",
          "tools/rules.py", "tools/lock.py", "tools/audit_selftest.py",
          "tools/check_js.py", "tools/check_generated.py", "tools/install_hooks.py",
          "tools/reslog.py", "tools/rollback_reviewed.py",
          ".github/workflows/rules.yml", ".claude/settings.json",
          "docs/rollback-floor.txt", "docs/RULE-OPERATION.md",
          "tools/selftest_uncovered.txt",
          # 第5回で「HASHEDから漏れていた」と指摘された分:
          #  Y-3/U-2/T-2/V-2/X-1 門番の自己テスト自体が無検査で、CASESに1行足すだけで
          #    任意のルールIDに「門番が見ている」証拠を作れた
          #  Y-2 検査器(check_generated)は入れたのに、その入力である生成器が野放しだった。
          #    生成器に4行足すだけで「手書きページが残ったまま緑」にできた
          #  X-5 RULES.md の中身が自由で、棚卸し日に不正な日付を書けば
          #    rules.problems() が例外で落ち、約30種別がHIGH1件に丸まる
          "tools/gate_selftest.py", "tools/gen_detail_pages.py", "tools/prerender.py",
          "docs/RULES.md", "tools/hooks/no_git_internal_write.py",
          # R-01〜R-08(2026-08-13): 攻撃側を縛る仕組み。ここが外れると
          # 「本物を触っていない」ことを誰も確かめないまま回が回る。
          "tools/redteam.py", "tools/hooks/no_redteam_write.py",
          "tools/hooks/_cmdline.py",
          # CC-1(第7回): 旧形式の証拠を例外扱いする一覧。守らないと
          # 「一覧に足す」だけで偽造の証拠が通ってしまう。
          "tools/reslog_legacy.txt", "tools/reslog.py",
          # 攻撃側に渡す条件の正本。回ごとに書き換わると回の比較ができない。
          "docs/redteam-brief.md")
# N-3(第4回): 縮小の記録 lock_reason.txt はどこにも守られておらず、
# 消しても誰も気づかなかった。必須ファイルに入れる。
# Y-5(第5回): research_log.json も無保護だった(「証拠つき」を手書きできる素材)。
PRESENT = ("docs/RULE-VIOLATIONS.md",
           "tools/audit_baseline.json", "tools/audit_baseline_reason.txt",
           "tools/lock_reason.txt", "tools/research_log.json",
           "docs/redteam-log.txt")
ARRAYS = {"characters.html": "generals", "characters-kyoku.html": "kyokuGenerals",
          "characters-ketsu.html": "ketsuGenerals"}


def _read(rel):
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        return None
    with io.open(p, encoding="utf-8", newline="") as f:
        return f.read()


def _sha(text):
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:16]


def check_names(only=None):
    """監査が出しうるチェック種別。**構文木から**集める。

    J-1 / M-1補(2026-08-13 第4回レッドチーム): ここは
    `add\\("([^"]+)"` という**テキストの正規表現**だった。
    そのためチェック本体を消して名前だけコメントに残せば「消えていない」ことになり、
    未カバーの24種別(S-03/S-04/S-05という対の関係の中核を含む)が
    門番もCIも緑のまま完全に無検査にできた。
    コメントも文字列リテラルも構文木には呼び出しとして現れないので、AST で数える。
    """
    import ast
    out = set()
    for rel, fn, argi in (("tools/audit_characters.py", "add", 0),
                          ("tools/rules.py", "append", 0)):
        # W-4/X-2(第5回): 「どのファイルが出した種別か」で絞れるようにする。
        # 門番の --accept が、飲んでよい指摘かを**語の列挙**で決めていたため。
        if only and rel not in only:
            continue
        src = _read(rel)
        if not src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name != fn:
                continue
            a = node.args[argi]
            # rules.py は out.append(("種別", ...)) の形
            if isinstance(a, ast.Tuple) and a.elts:
                a = a.elts[0]
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                if "%s" not in a.value:
                    out.add(a.value)
    return out


def current():
    """今のリポジトリから「あるはずのもの」を集める。"""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import rules as R
    R.ROOT = ROOT
    R.RULES = os.path.join(ROOT, "docs", "RULES.md")
    R.VIOL = os.path.join(ROOT, "docs", "RULE-VIOLATIONS.md")

    cats = check_names()

    # 武将の件数(母集団。減る方向を無条件で通さないため)
    counts = {}
    try:
        from audit_characters import extract_array
        for f, v in ARRAYS.items():
            counts[f] = len(extract_array(os.path.join(ROOT, f), v))
    except Exception as e:
        counts = {"__error__": str(e)}

    # 違反ログの各行(消したこと・書き換えたことを検出する)
    vio = {}
    for r in R.violations():
        if r["parse_error"]:
            continue
        vio["%s|%s" % (r["date"], r["id"])] = _sha(
            "|".join([r["cause"], r["sev"], r["what"], r["impact"]]))

    return {
        "checks": sorted(cats),
        "rule_ids": sorted(R.rule_ids()),
        "cause_tags": sorted(R.cause_tags()),
        "entry_counts": counts,
        "violations": vio,
        # U-4(第5回): ファイルが無くても空文字列のハッシュが入るので、
        # 「消えた」分岐が到達不能だった。無いときは None にする。
        "hooks": {f: (_sha(_read(f)) if _read(f) is not None else None) for f in HASHED},
        # U-3(第5回): ここは `sorted(PRESENT)` と**定数をそのまま返して**いた。
        # 同じ定数どうしを比べるので「必須ファイルが消えた」は原理的に発火せず、
        # 承認履歴(audit_baseline_reason.txt)も縮小履歴(lock_reason.txt)も
        # 消し放題で、出力は正常時と区別がつかなかった。実ファイルを見る。
        "present": sorted(f for f in PRESENT
                          if os.path.exists(os.path.join(ROOT, f))),
    }


def load():
    if not os.path.exists(PATH):
        return None
    with io.open(PATH, encoding="utf-8") as f:
        return json.load(f)


def problems():
    """(種別, 深刻度, 本文)。錠前と今の状態のズレを出す。"""
    lock = load()
    if lock is None:
        return [("錠前が無い", "HIGH",
                 "tools/checks.lock が無い。`python tools/lock.py --update` で作る")]
    cur = current()
    out = []

    def gone(kind, before, now, how):
        for x in sorted(set(before) - set(now)):
            out.append((kind, "HIGH", "「%s」が消えている。%s" % (x, how)))

    gone("監査チェックが消えた", lock["checks"], cur["checks"],
         "チェックを消すと、そのルールは誰も見ていないことになる。"
         "意図した削除なら `python tools/lock.py --update --reason \"...\"`")
    gone("ルールが索引から消えた", lock["rule_ids"], cur["rule_ids"],
         "索引に無いルールは「存在しない」扱いになる。消すのは廃止と同義")
    gone("根本原因タグが消えた", lock["cause_tags"], cur["cause_tags"],
         "過去の違反の分類が変わる")
    gone("必須ファイルが消えた", lock["present"], cur["present"],
         "門番か、その設定そのもの")

    # タグの追加は「2回目判定を外す」手口に使われたので、増えたことも知らせる
    for x in sorted(set(cur["cause_tags"]) - set(lock["cause_tags"])):
        out.append(("根本原因タグが増えた", "MID",
                    "「%s」が追加された。新しいタグを使うと、同じ原因の違反が"
                    "別々に数えられて2回目の停止が外れる。本当に別の原因か確かめる" % x))

    # 違反ログの行が消えた/書き換わった
    for k, h in sorted(lock["violations"].items()):
        if k not in cur["violations"]:
            out.append(("違反ログの行が消えた", "HIGH",
                        "%s の行が無くなっている。違反ログは消さない" % k))
        elif cur["violations"][k] != h:
            out.append(("違反ログの過去行が書き換わった", "HIGH",
                        "%s の内容(原因/区分/何をしたか/影響)が変わっている" % k))
    # 新しい行を錠前に取り込まないと、その行は「消しても気づかれない」ままになる。
    for k in sorted(set(cur["violations"]) - set(lock["violations"])):
        out.append(("違反ログの新しい行が錠前に無い", "MID",
                    "%s を `python tools/lock.py --update` で取り込む。"
                    "取り込むまで、この行は消しても検出されない" % k))

    # 母集団が減った
    for f, n in sorted(lock["entry_counts"].items()):
        m = cur["entry_counts"].get(f)
        if isinstance(n, int) and isinstance(m, int) and m < n:
            out.append(("武将の件数が減った", "HIGH",
                        "%s が %d体 → %d体。直せない指摘を、その武将ごと消していないか"
                        % (f, n, m)))

    # 門番の中身
    for f, h in sorted(lock["hooks"].items()):
        m = cur["hooks"].get(f)
        if m is None:
            out.append(("門番のファイルが消えた", "HIGH", f))
        elif m != h:
            out.append(("門番の中身が変わった", "HIGH",
                        "%s のハッシュが %s → %s。1行足すだけで全検査を無言化できるので、"
                        "変更は錠前の更新とセットで行う" % (f, h, m)))
    return out


def update(reason):
    lock = load()
    cur = current()
    shrink = []
    if lock:
        for key in ("checks", "rule_ids", "cause_tags", "present"):
            shrink += ["%s:%s" % (key, x) for x in set(lock[key]) - set(cur[key])]
        shrink += ["violations:" + k for k in set(lock["violations"]) - set(cur["violations"])]
        for f, n in lock["entry_counts"].items():
            m = cur["entry_counts"].get(f)
            if isinstance(n, int) and isinstance(m, int) and m < n:
                shrink.append("entry_counts:%s %d→%d" % (f, n, m))
        for f, h in lock["hooks"].items():
            if cur["hooks"].get(f) != h:
                shrink.append("hooks:" + f)
    # N-2 / J-2a(2026-08-13 第4回レッドチーム):
    # --accept 側には「理由10文字以上」と禁止領域があるのに、
    # 錠前を縮める入口には非空文字列1個で通る穴が残っていた。
    # 錠前は違反ログ・チェック種別・ルールID・母集団を守る最後の砦なので、同じ検査を入れる。
    if shrink:
        if not reason or len(reason.strip()) < 10:
            print("錠前を縮める変更が %d件ある。--reason \"なぜ減らすのか\"(10文字以上)が要る。"
                  % len(shrink))
            for x in shrink[:20]:
                print("  - " + x)
            return 1
        # 違反ログの行を消す・門番の中身を変えるのは、理由だけでは通さない。
        heavy = [x for x in shrink if x.startswith(("violations:", "hooks:"))]
        if heavy and "--i-know" not in sys.argv:
            print("[停止] 理由だけでは縮められないものが %d件ある:" % len(heavy))
            for x in heavy[:10]:
                print("  - " + x)
            print()
            print("違反ログの行と門番の中身は、消した記録そのもの/検査そのもの。")
            print("本当に必要なら --i-know を付ける(コミットメッセージにも書くこと)。")
            return 1
    with io.open(PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cur, f, ensure_ascii=False, indent=1, sort_keys=True)
    if shrink:
        io.open(REASON, "a", encoding="utf-8").write(
            "%s\t-%d\t%s\t%s\n" % (datetime.date.today().isoformat(), len(shrink),
                                   ",".join(shrink[:8]), reason))
        print("縮めた %d件の理由を tools/lock_reason.txt に追記した。" % len(shrink))
    print("錠前を更新した: チェック%d / ルール%d / タグ%d / 違反ログ%d行"
          % (len(cur["checks"]), len(cur["rule_ids"]),
             len(cur["cause_tags"]), len(cur["violations"])))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--update" in sys.argv:
        rs = ""
        if "--reason" in sys.argv:
            rs = sys.argv[sys.argv.index("--reason") + 1]
        sys.exit(update(rs))
    p = problems()
    if not p:
        lk = load() or {}
        print("錠前と一致している(チェック%d / ルール%d / 違反ログ%d行)"
              % (len(lk.get("checks", [])), len(lk.get("rule_ids", [])),
                 len(lk.get("violations", {}))))
        sys.exit(0)
    print("錠前とのズレ %d件" % len(p))
    for cat, sev, msg in p:
        print("  [%s] %s: %s" % (sev, cat, msg))
    sys.exit(1)
