# -*- coding: utf-8 -*-
"""コミット前の門番。`.git/hooks/pre-commit` から呼ばれる。

なぜ要るか(docs/RULE-OPERATION.md):
「コミット前に監査を実行する」というルール自体がエージェントの裁量に委ねられていて、
省略できてしまう状態だった。裁量を機械的に奪うのがこのスクリプトの目的。

止める条件:
 1. 監査(tools/audit_characters.py)のHIGHが、ベースラインより増えている
 2. ステージ済みの差分に `approved:true` の新規追加がある(RULES.md D-14)

警告だけ出す条件:
 3. データファイルを触っているのに prerender / gen_detail_pages を流していない疑い

ベースラインは tools/audit_baseline.json。既知の未解決分をここに置いておき、
「増えていないこと」だけを見る(全部ゼロにしてから運用開始、では永久に始まらないため)。
減ったときは更新を促すメッセージを出す。

    python tools/precommit_check.py             # 判定
    python tools/precommit_check.py --accept    # 今の状態をベースラインとして保存
"""
import io
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FINDINGS = os.path.join(ROOT, "tools", "audit_out", "findings.json")
BASELINE = os.path.join(ROOT, "tools", "audit_baseline.json")
DATA_FILES = ("characters.html", "characters-kyoku.html", "characters-ketsu.html",
              "skills.html", "assets/js/ixa-data.js")


def run(cmd):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def load(path):
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def key(x):
    return "%s|%s|%s" % (x["sev"], x["cat"], x["msg"])


def main():
    r = run([sys.executable, os.path.join("tools", "audit_characters.py")])
    if r.returncode != 0:
        print("監査ツールが失敗した。コミットを止める。")
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        return 1

    cur = load(FINDINGS)
    base = load(BASELINE)

    if "--accept" in sys.argv:
        with io.open(BASELINE, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1)
        print("ベースラインを更新した(%d件)。" % len(cur))
        return 0

    cur_keys = {key(x) for x in cur}
    base_keys = {key(x) for x in base}
    new_high = [x for x in cur if x["sev"] == "HIGH" and key(x) not in base_keys]
    fixed = [x for x in base if key(x) not in cur_keys]

    ng = False

    if new_high:
        ng = True
        print("=" * 62)
        print("[停止] 監査のHIGHが増えている: %d件" % len(new_high))
        print("=" * 62)
        for x in new_high:
            print("  [%s] %s" % (x["cat"], x["msg"][:150]))
        print()
        print("直してから再度コミットする。意図的に残す場合は理由を添えて")
        print("  python tools/precommit_check.py --accept")
        print("でベースラインに載せる(黙って通さない)。")

    # approved:true(赤丸)の自動付与は RULES.md D-14 で禁止。
    # ドキュメントやこのスクリプト自身にも同じ文字列が出るので、
    # **武将データのファイルに限って**見る(初回の試運転でドキュメントに誤反応した)。
    d = run(["git", "diff", "--cached", "-U0", "--"] + list(DATA_FILES))
    added_approved = [l for l in d.stdout.split("\n")
                      if l.startswith("+") and "approved:true" in l]
    if added_approved:
        ng = True
        print()
        print("=" * 62)
        print("[停止] approved:true を新規に追加している: %d行" % len(added_approved))
        print("=" * 62)
        for l in added_approved[:5]:
            print("  " + l[:150])
        print()
        print("赤丸はユーザーが明言したときだけ(RULES.md D-14)。")
        print("会話中に該当武将の承認発言があった場合のみ、この行を残してよい。")

    # 再生成の実行漏れ(警告のみ。止めるほどの確度で判定できないため)
    st = run(["git", "diff", "--cached", "--name-only"]).stdout.split()
    touched = [f for f in st if f in DATA_FILES]
    if touched:
        gen = [f for f in st if f.startswith(("busho/", "skill/")) or f == "sitemap.xml"]
        pr = [f for f in st if f.startswith("skills-") or f in ("characters.html", "characters-kyoku.html")]
        if not gen:
            print()
            print("[注意] データを触っているが busho/ skill/ sitemap.xml に差分が無い。")
            print("       python tools/gen_detail_pages.py を流し忘れていないか(RULES.md P-02)。")
        if not pr:
            print("[注意] prerender の対象ページに差分が無い。")
            print("       python tools/prerender.py を流し忘れていないか(RULES.md P-01)。")

    if fixed:
        print()
        print("[情報] ベースラインにあった %d件が解消している。" % len(fixed))
        print("       python tools/precommit_check.py --accept でベースラインを縮めておく。")

    if not ng:
        n_high = sum(1 for x in cur if x["sev"] == "HIGH")
        print("監査OK(HIGH %d件はベースライン内 / 全%d件)" % (n_high, len(cur)))
    return 1 if ng else 0


sys.exit(main())
