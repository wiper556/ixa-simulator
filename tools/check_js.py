# -*- coding: utf-8 -*-
"""各HTMLページの<script>が構文として通るかを見る。

なぜ要るか:
2026-08-12、`skills-takuetsu.html` の配列でカンマを`//`コメントの後ろに置いたため
区切りがコメントに飲まれ、**そのページのJSが丸ごと動かなくなっていた。**
一覧が空で表示されるだけなので、開いて見ない限り気づかない。
prerender は警告を出していたが、警告は止めないので素通りしていた。

データの中身が正しくても、ページが動かなければ意味が無い。
文法が通るかどうかは機械で判定できるので、コミット前に見る。

    python tools/check_js.py                 # ルート直下の全HTML
    python tools/check_js.py a.html b.html   # ファイルを指定
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARSE = ("src => { try { new Function(src); return 'ok'; }"
         " catch (e) { return e.message; } }")


def targets(argv):
    if argv:
        return argv
    return [n for n in sorted(os.listdir(ROOT))
            if n.endswith(".html") and os.path.isfile(os.path.join(ROOT, n))]


def main():
    from playwright.sync_api import sync_playwright
    files = targets(sys.argv[1:])
    bad = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto("about:blank")
        for n in files:
            path = n if os.path.isabs(n) else os.path.join(ROOT, n)
            if not os.path.exists(path) or not path.endswith(".html"):
                continue
            s = io.open(path, encoding="utf-8").read()
            # 外部読み込み(src=)と、JSONを入れるだけの型(ld+json等)は対象外
            for m in re.finditer(r"<script([^>]*)>([\s\S]*?)</script>", s):
                attr = m.group(1)
                if "src=" in attr:
                    continue
                t = re.search(r'type=["\']([^"\']+)', attr)
                if t and "javascript" not in t.group(1) and t.group(1) != "module":
                    continue
                r = pg.evaluate(PARSE, m.group(2))
                if r != "ok":
                    line = s[:m.start(1)].count("\n") + 1
                    bad.append((n, line, r))
                    print("  NG %s (%d行目からの<script>): %s" % (n, line, r))
        b.close()
    print("\n%d ページを検査、構文が壊れているブロック %d件" % (len(files), len(bad)))
    if bad:
        print("そのページのJSは丸ごと動かない(一覧が空で表示される)。")
        print("よくある原因: 配列の区切りカンマを `//` コメントの後ろに置いた(RULES.md T-06)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
