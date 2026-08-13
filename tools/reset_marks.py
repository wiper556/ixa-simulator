# -*- coding: utf-8 -*-
"""検証の印(approved / reviewedOk)を全件外して青丸に戻す(2026-08-13、ユーザー指示)。

■なぜ

「中途半端な登録で赤丸にしてあるのも大量にある」(ユーザー)。
赤丸は本来ユーザーが1件ずつ承認したという意味だが、2026-08-02に
40件以上の不正な自己承認が起きた経緯もあり、いま付いている147件が
どれも同じ基準を通ったとは言えない状態になっている。

信用できない赤丸が混ざったまま検証を続けるより、一度全部落として
同じ基準で通し直す。外した一覧は docs/marks-reset-2026-08-13.txt に残す。

**1回限りの道具。** 通常運用でこれを走らせることは無い。
(赤丸を付ける方向の自動化は D-14 で禁止。この道具は外す方向にしか動かない。)

    python tools/reset_marks.py --apply
"""
import collections
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRS = ("data/busho", "data/busho-kyoku", "data/busho-ketsu")
RECORD = os.path.join(ROOT, "docs", "marks-reset-2026-08-13.txt")
MARKS = ("approved", "reviewedOk")


def main(apply_it):
    removed = []
    for d in DIRS:
        full = os.path.join(ROOT, d)
        if not os.path.isdir(full):
            continue
        for fn in sorted(os.listdir(full)):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(full, fn)
            e = json.load(io.open(p, encoding="utf-8"),
                          object_pairs_hook=collections.OrderedDict)
            hit = [m for m in MARKS if m in e]
            if not hit:
                continue
            removed.append((d, e.get("no", "?"), e.get("name", "?"),
                            ",".join("%s=%s" % (m, e[m]) for m in hit)))
            for m in hit:
                del e[m]
            if apply_it:
                with io.open(p, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(e, f, ensure_ascii=False, indent=1)
                    f.write("\n")

    print("印が付いていた武将: %d体" % len(removed))
    if apply_it:
        with io.open(RECORD, "w", encoding="utf-8", newline="\n") as f:
            f.write("2026-08-13 に外した検証の印の一覧。\n")
            f.write("ユーザー指示で全件を青丸に戻した(作り直しに伴う棚卸し)。\n")
            f.write("ここに載っている武将は「一度は印が付いていた」記録であり、\n")
            f.write("再検証の免除にはならない。\n\n")
            for d, no, name, marks in removed:
                f.write("%s\t%s\t%s\t%s\n" % (d, no, name, marks))
        print("一覧を %s に書いた" % os.path.relpath(RECORD, ROOT))
    else:
        for r in removed[:10]:
            print("  ", r)
        print("  …(--apply で実行)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main("--apply" in sys.argv)
