# -*- coding: utf-8 -*-
"""ch が「未確認」の武将に、chGuess から章を書き込む。

**推定であることが分かるようにする。** ch にそのまま入れると確定値と
見分けが付かなくなるので、`chSource` に何を根拠にしたかを残し、notes にも
1行書く。あとで実物が分かったら ch を上書きし、chSource を消せばよい。

書き込むのは chGuess が1つに決まっているものだけ。「22章か23章」のように
割れているものは触らない(章末10日の先出しと区別できない帯)。

  python tools/fill_chapter.py            # 何件入るか見るだけ
  python tools/fill_chapter.py --write    # 書き込む

■ どれだけ当たるか(2026-08-16 時点)
章が分かっている146体で答え合わせして 141体(96.6%)。外れる5体はどれも
wiki のコメントが1〜4件しかない。**2025年8月以降は wiki の更新が
止まっており、この帯は当てにならない。**
"""
import collections
import glob
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 書き並べない。DBを足したときの書き忘れが繰り返し出ているので data/busho* を全部見る
DIRS = tuple(sorted(d for d in glob.glob(os.path.join(ROOT, "data", "busho*"))
                    if os.path.isdir(d)))
NOTE = ("2026-08-16: 章は ixawiki の該当カードページの一番古いコメントの"
        "日付から推定した(chSource=wikiOldestComment)。**ゲーム内で確認した値では"
        "ない。** 章が分かっている146体での的中は141体(96.6%)。実物が分かったら"
        "上書きして chSource を消すこと。")


def main(write=False):
    sys.stdout.reconfigure(encoding="utf-8")
    n = collections.Counter()
    for p in DIRS:
        if not os.path.isdir(p):
            continue
        for fn in sorted(os.listdir(p)):
            if not fn.endswith(".json"):
                continue
            fp = os.path.join(p, fn)
            j = json.load(io.open(fp, encoding="utf-8"),
                          object_pairs_hook=collections.OrderedDict)
            ch, g = j.get("ch"), j.get("chGuess")
            if ch and ch != "未確認":
                n["もう章がある"] += 1
                continue
            if not g:
                n["手がかりが無い"] += 1
                continue
            if "か" in g:
                n["2つに割れていて決められない"] += 1
                continue
            n["入れられる"] += 1
            if not write:
                continue
            j["ch"] = g
            j["chSource"] = "wikiOldestComment"
            j.setdefault("notes", []).append(NOTE)
            io.open(fp, "w", encoding="utf-8", newline="\n").write(
                json.dumps(j, ensure_ascii=False, indent=1) + "\n")
    for k, v in n.most_common():
        print("  %-26s %d体" % (k, v))
    if not write:
        print("\n(--write で書き込む)")


if __name__ == "__main__":
    main("--write" in sys.argv)
