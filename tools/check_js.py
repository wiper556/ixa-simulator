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
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARSE = ("src => { try { new Function(src); return 'ok'; }"
         " catch (e) { return e.message; } }")


def targets(argv):
    """既定はルート直下 + 生成物(busho/ skill/)。

    E-20(2026-08-12 第2回レッドチーム指摘): 以前はルート直下だけを見ていたので、
    生成した574ページは検査の外だった。現状インラインJSは無いが、
    生成器を変えれば入りうるし、JSON-LDは既に入っている。
    """
    if argv:
        return argv
    out = [n for n in sorted(os.listdir(ROOT))
           if n.endswith(".html") and os.path.isfile(os.path.join(ROOT, n))]
    for d in ("busho", "skill"):
        p = os.path.join(ROOT, d)
        if os.path.isdir(p):
            out += ["%s/%s" % (d, n) for n in sorted(os.listdir(p)) if n.endswith(".html")]
    return out


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
            for m in re.finditer(r"<script([^>]*)>([\s\S]*?)</script>", s):
                attr, body = m.group(1), m.group(2)
                line = s[:m.start(2)].count("\n") + 1
                t = re.search(r'type=["\']([^"\']+)', attr)
                kind = (t.group(1).strip().lower() if t else "")

                # E-20: JSONを入れる型は new Function に通らないので、JSONとして検査する。
                # 生成器が武将名を埋め込んでいるので、名前に " が混じれば壊れる。
                # 以前はどこも見ていなかった。
                if "json" in kind:
                    if body.strip():
                        try:
                            json.loads(body)
                        except Exception as e:
                            bad.append((n, line, "JSONとして壊れている: %s" % e))
                            print("  NG %s (%d行目 <script %s>): JSONが壊れている: %s"
                                  % (n, line, kind, e))
                    continue
                if kind and "javascript" not in kind and kind != "module":
                    continue
                if not body.strip():
                    continue

                r = pg.evaluate(PARSE, body)
                if r != "ok":
                    # E-20: src付きタグの中身はブラウザが実行しないので落とさないが、
                    # 「書いたのに動かない」を見逃さないよう知らせる。
                    if "src=" in attr:
                        print("  ?  %s (%d行目): src付き<script>の中身が構文エラー: %s"
                              % (n, line, r))
                        continue
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
