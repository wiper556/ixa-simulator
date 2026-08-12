# -*- coding: utf-8 -*-
"""コミット/マージ/プッシュ前の門番。`tools/hooks/` のフックから呼ばれる。

なぜ要るか(docs/RULE-OPERATION.md):
「コミット前に監査を実行する」というルール自体がエージェントの裁量に委ねられていて、
省略できてしまう状態だった。裁量を機械的に奪うのがこのスクリプトの目的。

2026-08-12 レッドチーム指摘への対応:
 A-1 `--accept` が理由なしで全件を丸ごと飲み込めた → 理由を必須にし、載せる内容を印字する
 A-2 HIGHしか見ずに「監査OK」と出していた → MIDの増減も必ず印字する
 A-3 approved:true の検出正規表現がネストで止まり8件を取りこぼしていた
     → 監査ツールと同じパーサでエントリを取る
 A-4 作業ツリーを検査してインデックスをコミットしていた
     → ステージ済みの内容を取り出して、そちらを検査する
 A-5 再生成の警告が、入力ファイル自身を出力に数えていて構造的に出なかった
 A-6 merge/rebase/cherry-pick/--no-verify が pre-commit を素通りできた
     → merge専用フックを足し、さらに push される中身そのものを検査する(--mode push)
 A-7 フック本体が .git/hooks/ にあり、消しても書き換えても痕跡が残らなかった
     → 正本を tools/hooks/ に置き、毎回一致を確かめる

止める条件:
 0. 導入済みフックが正本(tools/hooks/)と食い違っている
 1. 監査のHIGHがベースラインより増えた
 2. 新しく approved:true になった武将がいる(RULES.md D-14)
 3. attack-simulator.html を変えたのに SIMULATOR_VERSION を上げていない(P-03)
 4. ステージ済みの内容と作業ツリーが食い違っている(検査した物とコミットする物が別になる)

    python tools/precommit_check.py                 # = --mode commit
    python tools/precommit_check.py --mode push
    python tools/precommit_check.py --accept --reason "なぜ残すのか"
"""
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(ROOT, "tools", "audit_baseline.json")
DATA_FILES = ("characters.html", "characters-kyoku.html", "characters-ketsu.html",
              "skills.html", "assets/js/ixa-data.js")
ARRAY_OF = {"characters.html": "generals", "characters-kyoku.html": "kyokuGenerals",
            "characters-ketsu.html": "ketsuGenerals"}


# E-2: 展開したツリーにこれが無ければ、隠されたか壊れたと見なして止める。
# ここに挙げたものは「無くても監査が動いてしまう」= 消せば静かに検査を減らせるファイル。
REQUIRED = ("docs/RULES.md", "docs/RULE-VIOLATIONS.md", "docs/RULE-OPERATION.md",
            "docs/rollback-floor.txt",
            "tools/audit_baseline.json", "tools/audit_characters.py", "tools/rules.py",
            "tools/lock.py", "tools/checks.lock", "tools/check_js.py",
            "tools/audit_selftest.py", "tools/install_hooks.py",
            "tools/hooks/pre-commit", "tools/hooks/pre-merge-commit", "tools/hooks/pre-push",
            "tools/hooks/no_heredoc_backslash.py",
            ".github/workflows/rules.yml", ".claude/settings.json",
            "assets/css/site.css") + DATA_FILES


def run(cmd, cwd=ROOT, env=None):
    e = None
    if env:
        e = os.environ.copy()
        e.update(env)
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=e)


def load(path):
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def load_text(text):
    try:
        return json.loads(text)
    except Exception:
        return []


def key(x):
    return "%s|%s|%s" % (x["sev"], x["cat"], x["msg"])


MARK_SRC = r"""
import io, json, os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "tools"))
from audit_characters import extract_array
ARRAY_OF = {"characters.html": "generals", "characters-kyoku.html": "kyokuGenerals",
            "characters-ketsu.html": "ketsuGenerals"}
ap, rv = [], []
for f, var in ARRAY_OF.items():
    if not os.path.exists(f):
        continue
    for g in extract_array(f, var):
        if g.get("approved"):
            ap.append("%s:%s" % (f, g.get("no")))
        if g.get("reviewedOk"):
            rv.append("%s:%s" % (f, g.get("no")))
print("@@MARKS@@" + json.dumps({"approved": ap, "reviewed": rv}))
"""


def marks(path):
    """そのツリーの赤丸・黄丸の集合を、**そのツリーのコードで**数える。

    G-5(2026-08-13 第3回レッドチーム): 以前は作業ツリーの audit_characters を
    import していた。ステージしない7行を足すだけで赤丸の増分検査が黙り、
    出力は正常時と1文字も違わなかった。さらに tools/__pycache__ は gitignore
    されているので、.pyc だけ差し替えれば git status も空のまま抜けられた。
    import をやめ、取り出したツリーの中で -B(バイトコードを使わない)で走らせる。
    """
    r = run([sys.executable, "-B", "-c", MARK_SRC], cwd=path)
    for line in (r.stdout or "").split("\n"):
        if line.startswith("@@MARKS@@"):
            d = json.loads(line[len("@@MARKS@@"):])
            return set(d["approved"]), set(d["reviewed"])
    print("  ! %s の解析に失敗。検証マークのチェックを飛ばさず止める。" % path)
    print((r.stdout or "")[-400:])
    print((r.stderr or "")[-400:])
    return None, None


def approved_set(path):
    return marks(path)[0]


def gate_source_clean():
    """F-1(第3回): 門番のコードを**ステージせずに**書き換えると全検査が無効化できた。

    `dirty` 判定は「ステージ済みかつ作業ツリーでも変更されている」ものしか見ないので、
    ステージしないツール改造は素通りした。しかも事後に checkout すれば痕跡が残らない。
    検査する道具に未コミットの差分がある状態では、門番は動かない。
    """
    out = run(["git", "status", "--porcelain", "--",
               "tools", ".github", ".claude/settings.json"]).stdout
    bad = []
    for l in out.split("\n"):
        if len(l) < 4:
            continue
        path = l[3:].strip().strip('"')
        if path.startswith(("tools/audit_out/", "tools/__pycache__/")):
            continue
        # XY path。X=インデックス側 / Y=作業ツリー側。
        # ステージ済み(X)は検査対象ツリーに入るので問題ない。
        # 危ないのは「ステージしていない改変(Y)」と「追跡されていないファイル(??)」。
        if l[:2] == "??" or l[1] != " ":
            bad.append(l)
    if not bad:
        return True
    print("=" * 62)
    print("[停止] 検査に使う道具に、ステージしていない変更がある: %d件" % len(bad))
    print("=" * 62)
    for l in bad[:10]:
        print("  " + l)
    print()
    print("この状態だと、検査するコードとコミットされるコードが別になる。")
    print("第3回レッドチームは、これで全検査を無効化したうえで")
    print("正常時と1文字も違わない出力を出せることを実証している。")
    print("git add でそろえるか、退避してから実行する。")
    return False


def export_tree(ref, dest, require=True):
    """任意のコミットの中身を取り出す。push検査で HEAD / リモート側を見るのに使う。

    E-2(2026-08-12 第2回レッドチーム指摘): 以前は `git archive` を使っていたが、
    これは `.gitattributes` の `export-ignore` を尊重する。
    追跡外の `.git/info/attributes` に1行書くだけで、**git status をきれいに保ったまま**
    好きなファイルを検査の目から消せた(違反ログ・RULES.md・CSS など、
    「無くても監査が動く」ものは全部隠せた)。

    read-tree + checkout-index は export-ignore を見ないので、こちらを使う。
    そのうえで、消えていたら困るファイルの存在を毎回確かめる。
    """
    idx = os.path.join(tempfile.mkdtemp(prefix="idx_"), "index")
    env = {"GIT_INDEX_FILE": idx}
    if run(["git", "read-tree", ref], env=env).returncode != 0:
        return False
    if run(["git", "checkout-index", "-a", "-f", "--prefix=%s%s" % (dest, os.sep)],
           env=env).returncode != 0:
        return False
    if require:
        miss = [f for f in REQUIRED if not os.path.exists(os.path.join(dest, f))]
        if miss:
            print("=" * 62)
            print("[停止] 検査対象のツリーから必須ファイルが消えている: %d件" % len(miss))
            print("=" * 62)
            for f in miss[:10]:
                print("  " + f)
            print()
            print("消えていると、その分の検査が黙って行われなくなる。")
            print("`.gitattributes` / `.git/info/attributes` の export-ignore、")
            print("または本当に削除していないかを確認する。")
            return False
    return True


def audit_tree(path):
    """そのツリーで監査を回して findings を返す。回せなければ None。"""
    r = run([sys.executable, os.path.join("tools", "audit_characters.py")], cwd=path)
    if r.returncode != 0:
        print("監査ツールが失敗した。")
        print((r.stdout or "")[-1200:])
        print((r.stderr or "")[-1200:])
        return None
    return load(os.path.join(path, "tools", "audit_out", "findings.json"))


def baseline_of(tree):
    """E-1: ベースラインは**検査するツリーの中身**から読む。

    以前は作業ツリーの `tools/audit_baseline.json` を読んでいた。
    一方 findings は「ステージ済みの内容」から作っていたので、
    **baseline をステージせずに書き換えるだけで**HIGHの増分が消え、
    commit も push も通った。しかも baseline はコミットに入らないので痕跡が残らない。
    検査する側と比べる側は同じツリーから取る。
    """
    p = os.path.join(tree, "tools", "audit_baseline.json")
    if not os.path.exists(p):
        print("[注意] 検査対象ツリーに tools/audit_baseline.json が無い。空として扱う。")
        return []
    return load(p)


def hooks_ok():
    """A-7: 導入済みフックが正本と一致しているか。"""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    try:
        from install_hooks import diffs
    except Exception as e:
        print("[停止] tools/install_hooks.py を読めない(%s)。フックの正しさを確認できない。" % e)
        return False
    bad = diffs()
    if bad:
        print("=" * 62)
        print("[停止] 導入済みフックが正本(tools/hooks/)と食い違っている")
        print("=" * 62)
        for h, why in bad:
            print("  %-18s %s" % (h, why))
        print()
        print("  python tools/install_hooks.py   で入れ直す。")
        print("  意図して変えたなら tools/hooks/ 側を直してコミットする。")
        return False
    return True


def check_push():
    """A-6: push される中身そのものを検査する。ここまでの経路は問わない。

    pre-commit は rebase・cherry-pick・--no-verify で回避できる。
    回避してもここは通るので、外に出る内容は必ず一度は監査を通ることになる。
    """
    # フックの標準入力から <localref> <localsha> <remoteref> <remotesha> が来る
    pushing = []
    deleting = 0
    try:
        for line in sys.stdin.read().split("\n"):
            p = line.split()
            if len(p) != 4:
                continue
            if p[1] == "0" * 40:      # ブランチ削除。検査するツリーが無い
                deleting += 1
                continue
            pushing.append((p[0], p[1], p[2], p[3]))
    except Exception:
        pass
    if not pushing:
        if deleting:
            print("削除のみの push。検査するツリーが無いので何もしない。")
            return 0
        remote = run(["git", "rev-parse", "origin/master"]).stdout.strip()
        pushing = [("HEAD", "HEAD", "", remote or "")]

    ng = False
    for localref, local, remoteref, remote in pushing:
        tmp = tempfile.mkdtemp(prefix="prepush_")
        old = tempfile.mkdtemp(prefix="prepush_old_")
        try:
            if not export_tree(local, tmp):
                print("[停止] %s の中身を取り出せない。" % local[:12])
                return 1
            cur = audit_tree(tmp)
            if cur is None:
                return 1
            # E-1: ベースラインも押し出すツリーの中身から取る
            base_keys = {key(x) for x in baseline_of(tmp)}

            # E-5: 以前は監査HIGHと赤丸しか見ていなかったので、`--no-verify` で入れた
            # 構文エラーがそのまま外へ出た。押し出すツリーそのものにも check_js を回す。
            r = run([sys.executable, os.path.join("tools", "check_js.py")], cwd=tmp)
            if r.returncode != 0:
                ng = True
                print("=" * 62)
                print("[停止] push しようとしているページのJSが構文エラー(T-06/T-07)")
                print("=" * 62)
                print((r.stdout or "").strip()[-1200:])

            # I-9/I-10(第3回): 公開されるページとデータが一致しているか。
            # 生成物を手で書き換えても、再生成を忘れても、これまで通っていた。
            # 実際に生成して比べる(約1分)。commitのたびには重いのでpushだけ。
            r = run([sys.executable, os.path.join("tools", "check_generated.py"), local])
            if r.returncode != 0:
                ng = True
                print((r.stdout or "").strip()[-1500:])

            new_high = [x for x in cur if x["sev"] == "HIGH" and key(x) not in base_keys]
            new_mid = [x for x in cur if x["sev"] != "HIGH" and key(x) not in base_keys]
            if new_high:
                ng = True
                print("=" * 62)
                print("[停止] push しようとしている中身で監査のHIGHが増えている: %d件" % len(new_high))
                print("=" * 62)
                for x in new_high[:15]:
                    print("  [%s] %s" % (x["cat"], x["msg"][:150]))
                print()
                print("pre-commit を通していない経路(rebase/cherry-pick/--no-verify/merge)で")
                print("入った可能性がある。直してから push する。")

            # E-6: リモート側が無い(新規ブランチ・タグ)とき、以前は「比較対象=空集合」
            # として扱っていたので、赤丸147件が全部「新規」に見えて必ず止まった。
            # worktree運用では日常的に起きるので、逃げ道が --no-verify しか無くなっていた。
            # 比較の基準は「その内容が既にリモートのどこかに在るか」なので、
            # remote が無いときは共通の祖先(既定ブランチとのmerge-base)を使う。
            base_ref = remote if (remote and remote != "0" * 40) else None
            if base_ref is None:
                for cand in ("origin/master", "origin/main", "master", "main"):
                    if run(["git", "rev-parse", "--verify", "-q", cand]).returncode == 0:
                        mb = run(["git", "merge-base", local, cand]).stdout.strip()
                        base_ref = mb or cand
                        break
            now, now_rv = marks(tmp)
            before = before_rv = None
            if base_ref and export_tree(base_ref, old, require=False):
                before, before_rv = marks(old)
            # I-3(第3回): 黄丸(reviewedOk)を止める仕組みが1つも無く、
            # 107体を一括で「検証済み」に昇格させても新規HIGHは1件だけだった。
            # 止めはしないが、増分は必ず名前つきで出す。
            gained_rv = sorted((now_rv or set()) - (before_rv or set()))
            if gained_rv:
                print()
                print("[確認] 新しく黄丸(reviewedOk)になった武将: %d件" % len(gained_rv))
                for x in gained_rv[:15]:
                    print("   " + x)
                if len(gained_rv) > 15:
                    print("   ... ほか%d件" % (len(gained_rv) - 15))
                print("   黄丸は manual A-1〜A-5 を通した武将だけ。まとめて付けていないか。")
            if before is None:
                print()
                print("[注意] 比較対象(%s)を取れないので、赤丸の増分は判定していない。"
                      % (base_ref or "なし"))
                before = now or set()
            gained = sorted((now or set()) - before)
            if gained:
                ng = True
                print()
                print("=" * 62)
                print("[停止] %s に無い approved:true が %d件含まれている"
                      % (base_ref[:12] if base_ref else "比較対象", len(gained)))
                print("=" * 62)
                for x in gained[:10]:
                    print("  " + x)
                print()
                print("赤丸はユーザーが明言したときだけ(RULES.md D-14)。")

            print()
            print("push検査(%s): HIGH %d件(新規%d) / MID %d件(新規%d) / 赤丸 %d件"
                  % (local[:12],
                     sum(1 for x in cur if x["sev"] == "HIGH"), len(new_high),
                     sum(1 for x in cur if x["sev"] != "HIGH"), len(new_mid),
                     len(now or [])))
            if new_mid:
                for x in new_mid[:10]:
                    print("  + [MID] %s %s" % (x["cat"], x["msg"][:110]))
                if len(new_mid) > 10:
                    print("  ... ほか%d件" % (len(new_mid) - 10))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(old, ignore_errors=True)
    return 1 if ng else 0


def main():
    mode = "commit"
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1]
    # E-23: 未知の mode を黙って commit 扱いにしていた。綴り違いで検査が変わるのは危ない。
    if mode not in ("commit", "merge", "push"):
        print("--mode は commit / merge / push のいずれか。受け取った値: %r" % mode)
        return 1

    # 出力に「いつ・どのツリーの結果か」を必ず入れる。
    # I-12(第3回): これが無いと、きれいだった時点の本物の出力を後から貼れてしまう。
    head = run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
    tree = run(["git", "write-tree"]).stdout.strip()[:12]
    print("[%s] mode=%s HEAD=%s tree=%s"
          % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), mode, head, tree))

    # A-7: どのモードでも、まずフックそのものが正本どおりかを見る
    if not hooks_ok():
        return 1
    # F-1: 検査に使う道具が未コミットで書き換わっていないか
    if not gate_source_clean():
        return 1
    if mode == "push":
        return check_push()

    staged = run(["git", "diff", "--cached", "--name-only"]).stdout.split()
    ng = False

    # A-4: 検査対象はステージ済みの内容。作業ツリーとの食い違いを先に潰す。
    dirty = [f for f in run(["git", "diff", "--name-only"]).stdout.split() if f in staged]
    if dirty:
        print("=" * 62)
        print("[停止] ステージ済みの内容と作業ツリーが食い違っている: %d件" % len(dirty))
        print("=" * 62)
        for f in dirty[:8]:
            print("  " + f)
        print()
        print("この状態だと、検査する物とコミットされる物が別になる。")
        print("git add でそろえてから再実行する。")
        return 1

    # ステージ済みのツリーを取り出して、そこで監査する
    tmp = tempfile.mkdtemp(prefix="precommit_")
    try:
        r = run(["git", "checkout-index", "-a", "-f", "--prefix=%s%s" % (tmp, os.sep)])
        if r.returncode != 0:
            print("[停止] ステージ内容を取り出せない。作業ツリーで代用すると、")
            print("検査する物とコミットされる物が別になるので進めない。")
            print((r.stderr or "")[-600:])
            return 1
        target = tmp
        miss = [f for f in REQUIRED if not os.path.exists(os.path.join(target, f))]
        if miss:
            print("[停止] ステージ済みツリーから必須ファイルが消えている: %s" % ", ".join(miss[:6]))
            return 1
        r = run([sys.executable, os.path.join("tools", "audit_characters.py")], cwd=target)
        if r.returncode != 0:
            print("監査ツールが失敗した。コミットを止める。")
            print((r.stdout or "")[-1500:])
            print((r.stderr or "")[-1500:])
            return 1
        cur = load(os.path.join(target, "tools", "audit_out", "findings.json"))
        approved_now, reviewed_now = marks(target)
        base = baseline_of(target)      # E-1: 比べる相手も同じツリーから
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if "--accept" in sys.argv:
        # A-1: 理由を必須にし、何を飲み込むのかを印字してから書く
        if "--reason" not in sys.argv:
            print("--reason \"なぜ残すのか\" が要る。理由なしでベースラインは動かせない。")
            return 1
        reason = sys.argv[sys.argv.index("--reason") + 1]
        # H-11/G-11(第3回): 理由 "-" の1文字で全件を1発で飲めた。
        if len(reason.strip()) < 10:
            print("理由が短すぎる(10文字以上)。何を、なぜ残すのかを書く。")
            return 1
        # I-1(第3回): 運用そのものを見ているHIGH(2回目の停止・錠前・フック)まで
        # ベースラインに載せられた。データの指摘を飲むついでに規律が消えるので、
        # 運用側の種別は載せられないようにする。
        BANNED = ("2回目の違反", "違反ログ", "フックが正本と違う", "門番",
                  "錠前", "監査チェックが消えた", "ルールが索引から消えた",
                  "必須ファイルが消えた", "武将の件数が減った", "監査に足した")
        blocked = [x for x in cur if key(x) not in {key(y) for y in base}
                   and any(b in x["cat"] for b in BANNED)]
        if blocked:
            print("[停止] 運用そのものを見ている指摘は --accept で飲めない: %d件" % len(blocked))
            for x in blocked[:10]:
                print("  [%s] %s %s" % (x["sev"], x["cat"], x["msg"][:100]))
            print()
            print("これらは「規律が壊れている」という報せなので、直すしかない。")
            return 1
        base_keys = {key(x) for x in base}
        add_ = [x for x in cur if key(x) not in base_keys]
        drop = [x for x in base if key(x) not in {key(y) for y in cur}]
        print("ベースラインに載せる: %d件 / 外れる: %d件" % (len(add_), len(drop)))
        for x in add_[:20]:
            print("  + [%s] %s %s" % (x["sev"], x["cat"], x["msg"][:110]))
        if len(add_) > 20:
            print("  ... ほか%d件" % (len(add_) - 20))
        with io.open(BASELINE, "w", encoding="utf-8") as f:
            json.dump({"reason": reason, "findings": cur} if False else cur,
                      f, ensure_ascii=False, indent=1)
        io.open(os.path.join(ROOT, "tools", "audit_baseline_reason.txt"), "a",
                encoding="utf-8").write("%s\t+%d/-%d\t%s\n"
                                        % (run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip(),
                                           len(add_), len(drop), reason))
        # E-23: 以前はどちらもステージしなかったので、違反を通したコミットと
        # 承認の記録が別のコミットに分かれた(記録だけ永久に入らないこともあった)。
        run(["git", "add", "tools/audit_baseline.json", "tools/audit_baseline_reason.txt"])
        print("理由を tools/audit_baseline_reason.txt に追記し、両方をステージした。")
        print("このコミットに含めること。")
        return 0

    # F-2/H-2(第3回、2体が指摘): --accept は「使ってもよい入口」であって必須ではなく、
    # baseline はただの追跡ファイルだった。新しく出た findings をそのまま追記して
    # git add すれば commit も push も CI も通り、理由ファイルには1行も残らなかった。
    # ベースラインが増えているのに、同じコミットで理由が増えていなければ止める。
    if "tools/audit_baseline.json" in staged:
        head_base = load_text(run(["git", "show", "HEAD:tools/audit_baseline.json"]).stdout)
        n_before = len(head_base)
        n_after = len(base)
        if n_after > n_before:
            r_before = run(["git", "show", "HEAD:tools/audit_baseline_reason.txt"]).stdout or ""
            r_after = run(["git", "show", ":tools/audit_baseline_reason.txt"]).stdout or ""
            if len(r_after.strip().split("\n")) <= len(r_before.strip().split("\n")):
                ng = True
                print("=" * 62)
                print("[停止] ベースラインが %d件 → %d件 に増えているのに理由が無い"
                      % (n_before, n_after))
                print("=" * 62)
                print("手で書き足さず、次を使う:")
                print('  python tools/precommit_check.py --accept --reason "なぜ残すのか"')
                print("(理由は tools/audit_baseline_reason.txt に残り、同じコミットに入る)")

    cur_keys = {key(x) for x in cur}
    base_keys = {key(x) for x in base}
    new_high = [x for x in cur if x["sev"] == "HIGH" and key(x) not in base_keys]
    new_mid = [x for x in cur if x["sev"] != "HIGH" and key(x) not in base_keys]
    fixed = [x for x in base if key(x) not in cur_keys]

    if new_high:
        ng = True
        print("=" * 62)
        print("[停止] 監査のHIGHが増えている: %d件" % len(new_high))
        print("=" * 62)
        for x in new_high:
            print("  [%s] %s" % (x["cat"], x["msg"][:150]))
        print()
        print("直してから再度コミットする。意図的に残すなら理由を添えて")
        print('  python tools/precommit_check.py --accept --reason "..."')

    # A-3: エントリ境界をJS評価で取る
    if approved_now is None:
        ng = True
    else:
        head = tempfile.mkdtemp(prefix="head_")
        try:
            run(["git", "checkout-index", "-a", "-f", "--prefix=%s%s" % (head, os.sep)],
                cwd=ROOT)
            # HEAD の内容を取り出す
            for f in ARRAY_OF:
                o = run(["git", "show", "HEAD:" + f]).stdout
                if o:
                    io.open(os.path.join(head, f), "w", encoding="utf-8", newline="").write(o)
            approved_before = approved_set(head)
        finally:
            shutil.rmtree(head, ignore_errors=True)
        gained = sorted(approved_now - (approved_before or set()))
        if gained:
            ng = True
            print()
            print("=" * 62)
            print("[停止] 新しく approved:true になった武将: %d件" % len(gained))
            print("=" * 62)
            for x in gained[:10]:
                print("  " + x)
            print()
            print("赤丸はユーザーが明言したときだけ(RULES.md D-14)。")
        print("[確認] 赤丸の総数: %d件(HEAD %d件)"
              % (len(approved_now), len(approved_before or [])))

    # T-06 / T-07: ステージしたHTMLの<script>が構文として通るか。
    # データが正しくても文法が壊れればそのページは丸ごと動かない(一覧が空で出る)。
    # 監査は中身の整合しか見ないので、ここで止める。
    html = [f for f in staged if f.endswith(".html") and os.sep not in f.replace("/", os.sep)]
    if html:
        r = run([sys.executable, os.path.join("tools", "check_js.py")] + html)
        if r.returncode != 0:
            ng = True
            print()
            print("=" * 62)
            print("[停止] ステージしたページのJSが構文エラー(T-06/T-07)")
            print("=" * 62)
            print((r.stdout or "").strip()[-1200:])

    # W-13 / W-14: ルールの変更は作業と分ける。改定は索引と原文の両方を直す。
    #
    # この2件は「根本原因=手順の自己改変」で2回続いた。作業のついでに規則を書き換えると、
    # 変更が作業差分に埋もれて確認を取る機会そのものが消える。
    # 全部は機械で見られないが、以下の2つは見える。
    # E-12: 以前は「作業」を DATA_FILES + busho/ + skill/ + シミュレーターに限っていたので、
    # CSS・トップページ・一覧の prerender 出力はルール変更と同梱できた。
    # 列挙する側を反転させる。W-13が守りたいのは
    # 「サイトに出る成果物の変更に、規則の変更を紛れ込ませない」なので、
    # **サイトに出るもの以外を除外**する形にする。
    # G-10(第3回): rule_docs が `docs/RULE`(大文字)始まりだけだったので、
    # `docs/rollback-floor.txt`(巻き戻しの下限)と `.claude/agents/*.md`(エージェント定義)は
    # 「ルール文書」にも「作業」にも入らず、データ更新コミットに同梱できた。
    # 下限を「いまここまで確認した」に書き換える操作が、作業に紛れ込ませられた。
    rule_docs = [f for f in staged
                 if f.startswith(("docs/RULE", "docs/rollback-floor",
                                  ".claude/agents/", ".github/workflows/"))
                 or f in ("docs/character-registration-manual.md",
                          ".claude/settings.json", "tools/checks.lock")]
    NOT_WORK = ("docs/", "tools/", ".github/", ".claude/", ".gitignore", "README")
    work = [f for f in staged if not f.startswith(NOT_WORK)]
    if rule_docs and work:
        ng = True
        print()
        print("=" * 62)
        print("[停止] ルール文書の変更と作業の変更が同じコミットに混ざっている(W-13)")
        print("=" * 62)
        for f in rule_docs:
            print("  ルール  " + f)
        for f in work[:6]:
            print("  作業    " + f)
        print()
        print("ルールの変更は作業と分けて、先に確認を取る。")
        print("コミットを分ける: git reset HEAD <ルール文書> して作業だけ先に入れる。")
    if "docs/RULES.md" in staged and len(rule_docs) == 1:
        print()
        print("[注意] 索引(docs/RULES.md)だけを変更している。意味を変えたなら、"
              "原文側も同じコミットで直す(W-14)。列の追記だけならこの注意は無視してよい。")

    # P-03
    # E-13: 以前は差分に文字列 SIMULATOR_VERSION が現れたかだけを見ていたので、
    # HTMLコメントにその単語を書くだけで通った。値そのものを HEAD と比べる。
    if "attack-simulator.html" in staged:
        def simver(text):
            m = re.search(r"SIMULATOR_VERSION\s*=\s*['\"]([^'\"]+)", text or "")
            return m.group(1) if m else None
        before = simver(run(["git", "show", "HEAD:attack-simulator.html"]).stdout)
        after = simver(run(["git", "show", ":attack-simulator.html"]).stdout)
        if after is None:
            ng = True
            print()
            print("[停止] attack-simulator.html から SIMULATOR_VERSION を読み取れない(P-03)")
        elif before == after:
            ng = True
            print()
            print("=" * 62)
            print("[停止] attack-simulator.html を変えたが SIMULATOR_VERSION が %s のまま" % after)
            print("=" * 62)
            print("右下のバッジを同じコミットで更新する(RULES.md P-03)。軽微な変更も対象。")

    # I-10(第3回): ここには「再生成し忘れたか」を推測する警告が2つあったが、
    # どちらも構造的に空振りしていた。P-01は characters.html に PRERENDER という
    # 文字列があるかを見ていたが、その文字列は常駐しているので入力自身が出力に数えられ、
    # P-02は無関係な生成物1枚をステージするだけで満たせた。
    # 推測をやめ、push のときに実際に再生成して差分を見る(tools/check_generated.py)。
    if [f for f in staged if f in DATA_FILES]:
        print("\n[注意] データを触った。prerender と gen_detail_pages を回すこと(P-01/P-02)。")
        print("       回し忘れは push のときに検査される(tools/check_generated.py、約1分)。")

    # I-9(第3回): 生成ページを手で書き換えても通った。
    # 再生成の差分検査は「マーカーの間」しか見ないので、外側への追記は捕まえられない。
    # 生成物だけが変わっていて、データも生成器も触っていないなら、手で書いたということ。
    gen_only = [f for f in staged if f.startswith(("busho/", "skill/"))]
    if gen_only:
        cause = [f for f in staged
                 if f in DATA_FILES or f.startswith("tools/") or f == "sitemap.xml"]
        if not cause:
            ng = True
            print()
            print("=" * 62)
            print("[停止] 生成物だけが変わっている: %d件" % len(gen_only))
            print("=" * 62)
            for f in gen_only[:8]:
                print("  " + f)
            print()
            print("busho/ と skill/ は生成物なので手で編集しない(P-02)。")
            print("直すのはデータ側(characters*.html / skills.html)で、そのあと再生成する。")

    # A-2: MIDも必ず出す。「監査OK」で丸めない。
    print()
    print("監査: HIGH %d件(新規%d) / MID %d件(新規%d) / 合計%d件 %s"
          % (sum(1 for x in cur if x["sev"] == "HIGH"), len(new_high),
             sum(1 for x in cur if x["sev"] != "HIGH"), len(new_mid), len(cur),
             "← MIDが増えている" if new_mid else ""))
    if new_mid:
        for x in new_mid[:10]:
            print("  + [MID] %s %s" % (x["cat"], x["msg"][:110]))
        if len(new_mid) > 10:
            print("  ... ほか%d件" % (len(new_mid) - 10))
    if fixed:
        print("[情報] ベースラインにあった %d件が解消。--accept --reason で縮めておく。" % len(fixed))
    return 1 if ng else 0


sys.exit(main())
