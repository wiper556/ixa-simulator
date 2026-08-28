# -*- coding: utf-8 -*-
"""ページを実際にブラウザで開いて、JSが実行時に落ちないか見る。

なぜ要るか:
`check_js.py` は **構文が通るかどうかしか見ていない。** 構文が正しくても
実行時に落ちれば、そこから先のコードは一行も動かない。落ち方によっては
一覧が空のまま表示されるだけなので、開いて見ない限り気づかない
(check_js.py 自身がまさにその失敗を防ぐために作られたのに、半分しか防げていなかった)。

2026-08-29 に実際に見つかった3件:
  ・attack-simulator.html
      `initCandFilter()` を CF_RARITY の宣言より **前** で呼んでいた。
      「初期化前に参照した」で止まり、**同じ script の残り751行が動かず**、
      うぐさんの依頼で作った「候補の絞り込み」のチェック欄が丸ごと空だった。
  ・skills-cost.html / skills-mujin.html
      振り分けの案内ページに作り替えたのに表を組み立てるスクリプトが残っており、
      要素が見つからず開くたびに例外が出ていた。

いずれも check_js.py は通っていた。

    python tools/smoke_pages.py           # ルート直下 + 詳細ページの抜き取り
    python tools/smoke_pages.py --all     # 生成した詳細ページも全部(遅い)
    python tools/smoke_pages.py a.html    # ファイル指定

見逃すもの: 画面に出た後にユーザーが操作して初めて動くコードは通らない。
ここで見ているのは「開いた時点で落ちないか」だけ。
"""
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 抜き取りの間隔。詳細ページは同じ型から作るので、全部見なくても型の壊れは出る。
STRIDE = 40


def targets(argv):
    args = [a for a in argv if not a.startswith("--")]
    if args:
        return args
    root = sorted(os.path.basename(f) for f in glob.glob(os.path.join(ROOT, "*.html")))
    sub = []
    for d in ("busho", "skill"):
        v = sorted(glob.glob(os.path.join(ROOT, d, "*.html")))
        sub += v if "--all" in argv else v[::STRIDE]
    return root + [os.path.relpath(f, ROOT).replace(os.sep, "/") for f in sub]


def main(argv):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright が無いので飛ばす。")
        return 0
    files = targets(argv)
    bad = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for f in files:
            path = os.path.join(ROOT, f.replace("/", os.sep))
            if not os.path.exists(path):
                print("  無い: %s" % f)
                continue
            pg = b.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append("throw: " + str(e).split("\n")[0][:200]))
            pg.on("console", lambda m: errs.append("console.error: " + m.text[:200])
                  if m.type == "error" else None)
            try:
                pg.goto("file:///" + path.replace("\\", "/"), timeout=120000, wait_until="load")
                pg.wait_for_timeout(400)
            except Exception as e:
                errs.append("開けない: " + str(e).split("\n")[0][:200])
            pg.close()
            # 外部から読む広告や埋め込みが出す苦情は、うちのコードの誤りではない
            errs = [x for x in errs if "Content Security Policy" not in x
                    and "net::ERR_" not in x and "favicon" not in x]
            if errs:
                bad.append((f, errs))
                print("  NG %-34s %s" % (f, errs[0]))
    print()
    print("開いたページ %d枚 / 実行時に落ちた %d枚" % (len(files), len(bad)))
    if bad:
        print()
        print("**構文は通っている。開いて初めて分かる壊れ方。**")
        print("落ちた場所から後ろは、同じ <script> の中なら一行も動かない。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
