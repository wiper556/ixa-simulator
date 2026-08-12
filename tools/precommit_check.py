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
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(ROOT, "tools", "audit_baseline.json")
DATA_FILES = ("characters.html", "characters-kyoku.html", "characters-ketsu.html",
              "skills.html", "assets/js/ixa-data.js")
ARRAY_OF = {"characters.html": "generals", "characters-kyoku.html": "kyokuGenerals",
            "characters-ketsu.html": "ketsuGenerals"}


def run(cmd, cwd=ROOT):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def load(path):
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def key(x):
    return "%s|%s|%s" % (x["sev"], x["cat"], x["msg"])


def approved_set(path):
    """A-3: 正規表現でなく監査ツールと同じJS評価でエントリを取る。
    ネストした {} でエントリ境界を見失わないため。"""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from audit_characters import extract_array
    out = set()
    for f, var in ARRAY_OF.items():
        p = os.path.join(path, f)
        if not os.path.exists(p):
            continue
        try:
            for g in extract_array(p, var):
                if g.get("approved"):
                    out.add("%s:%s" % (f, g.get("no")))
        except Exception as e:
            print("  ! %s の解析に失敗(%s)。approvedチェックを飛ばさず止める。" % (f, e))
            return None
    return out


def export_tree(ref, dest):
    """任意のコミットの中身を取り出す。push検査で HEAD / リモート側を見るのに使う。"""
    r = subprocess.run(["git", "archive", "--format=tar", ref], cwd=ROOT, capture_output=True)
    if r.returncode != 0:
        return False
    with tarfile.open(fileobj=io.BytesIO(r.stdout)) as t:
        try:
            t.extractall(dest, filter="data")
        except TypeError:      # filter は Python 3.12 から
            t.extractall(dest)
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
    try:
        for line in sys.stdin.read().split("\n"):
            p = line.split()
            if len(p) == 4 and p[1] != "0" * 40:
                pushing.append((p[1], p[3]))
    except Exception:
        pass
    if not pushing:
        remote = run(["git", "rev-parse", "origin/master"]).stdout.strip()
        pushing = [("HEAD", remote or "")]

    ng = False
    base = load(BASELINE)
    base_keys = {key(x) for x in base}
    for local, remote in pushing:
        tmp = tempfile.mkdtemp(prefix="prepush_")
        old = tempfile.mkdtemp(prefix="prepush_old_")
        try:
            if not export_tree(local, tmp):
                print("[停止] %s の中身を取り出せない。" % local[:12])
                return 1
            cur = audit_tree(tmp)
            if cur is None:
                return 1
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

            now = approved_set(tmp)
            before = set()
            if remote and remote != "0" * 40 and export_tree(remote, old):
                before = approved_set(old) or set()
            gained = sorted((now or set()) - before)
            if gained:
                ng = True
                print()
                print("=" * 62)
                print("[停止] リモートに無い approved:true が %d件含まれている" % len(gained))
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

    # A-7: どのモードでも、まずフックそのものが正本どおりかを見る
    if not hooks_ok():
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
        target = tmp if r.returncode == 0 else ROOT
        if target == ROOT:
            print("[注意] ステージ内容を取り出せなかったので作業ツリーを検査する。")
        r = run([sys.executable, os.path.join("tools", "audit_characters.py")], cwd=target)
        if r.returncode != 0:
            print("監査ツールが失敗した。コミットを止める。")
            print((r.stdout or "")[-1500:])
            print((r.stderr or "")[-1500:])
            return 1
        cur = load(os.path.join(target, "tools", "audit_out", "findings.json"))
        approved_now = approved_set(target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    base = load(BASELINE)

    if "--accept" in sys.argv:
        # A-1: 理由を必須にし、何を飲み込むのかを印字してから書く
        if "--reason" not in sys.argv:
            print("--reason \"なぜ残すのか\" が要る。理由なしでベースラインは動かせない。")
            return 1
        reason = sys.argv[sys.argv.index("--reason") + 1]
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
        print("理由を tools/audit_baseline_reason.txt に追記した。")
        return 0

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
    rule_docs = [f for f in staged
                 if f.startswith("docs/RULE") or f == "docs/character-registration-manual.md"]
    work = [f for f in staged if f in DATA_FILES
            or f.startswith(("busho/", "skill/")) or f == "attack-simulator.html"]
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
    sim = run(["git", "diff", "--cached", "-U0", "--", "attack-simulator.html"]).stdout
    if sim.strip() and not any(l.startswith("+") and "SIMULATOR_VERSION" in l
                               for l in sim.split("\n")):
        ng = True
        print()
        print("=" * 62)
        print("[停止] attack-simulator.html を変えたが SIMULATOR_VERSION が上がっていない")
        print("=" * 62)
        print("右下のバッジを同じコミットで更新する(RULES.md P-03)。軽微な変更も対象。")

    # A-5: 入力ファイルを出力に数えていたので警告が構造的に出なかった。出力集合から除く。
    if [f for f in staged if f in DATA_FILES]:
        gen = [f for f in staged if f.startswith(("busho/", "skill/")) or f == "sitemap.xml"]
        pr = [f for f in staged if f.startswith("skills-") and f.endswith(".html")]
        pr += [f for f in staged if f in ("characters.html", "characters-kyoku.html")
               and "PRERENDER" in (run(["git", "show", ":" + f]).stdout or "")]
        if not gen:
            print("\n[注意] データを触ったが busho/ skill/ sitemap.xml に差分が無い。"
                  " gen_detail_pages.py の実行漏れ(P-02)。")
        if not pr:
            print("[注意] prerender の出力ページに差分が無い。"
                  " prerender.py の実行漏れ(P-01)。")

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
