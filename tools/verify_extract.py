# -*- coding: utf-8 -*-
"""切り出したJSONが、ページ本体の配列と1バイトも違わないことを確かめる。

作り直しの第1工程(extract_data.py)の受け入れ検査。notes / note は
コメントから起こした新しいフィールドなので、比較の前に取り除く。

    python tools/verify_extract.py
"""
import collections
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from extract_data import TARGETS, evaluate_array, safe_name  # noqa: E402


def drop_notes(x):
    if isinstance(x, dict):
        return collections.OrderedDict(
            (k, drop_notes(v)) for k, v in x.items() if k not in ("notes", "note"))
    if isinstance(x, list):
        return [drop_notes(v) for v in x]
    return x


def main():
    bad = 0
    for page, array, outdir, keyfld in TARGETS:
        if not os.path.exists(os.path.join(ROOT, page)):
            continue
        live = evaluate_array(page, array)
        d = os.path.join(ROOT, outdir)
        miss = 0
        for entry in live:
            key = safe_name(str(entry[keyfld]))
            p = os.path.join(d, key + ".json")
            if not os.path.exists(p):
                print("  [欠落] %s / %s" % (outdir, key))
                miss += 1
                continue
            saved = json.load(io.open(p, encoding="utf-8"),
                              object_pairs_hook=collections.OrderedDict)
            a = json.dumps(entry, ensure_ascii=False, sort_keys=False)
            b = json.dumps(drop_notes(saved), ensure_ascii=False, sort_keys=False)
            if a != b:
                miss += 1
                print("  [不一致] %s / %s" % (outdir, key))
                for i, (ca, cb) in enumerate(zip(a, b)):
                    if ca != cb:
                        print("     本体: ...%s..." % a[max(0, i - 60):i + 60])
                        print("     JSON: ...%s..." % b[max(0, i - 60):i + 60])
                        break
                else:
                    print("     長さ違い 本体%d / JSON%d" % (len(a), len(b)))
        n_files = len([x for x in os.listdir(d) if x.endswith(".json")])
        extra = n_files - len(live)
        print("  %-24s 本体%3d件 / JSON%3d件 / 不一致%d件%s"
              % (page, len(live), n_files, miss,
                 ("  ★余分なファイル%d件" % extra) if extra else ""))
        bad += miss + max(0, extra)
    print("不一致・欠落・余分 合計 %d件" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
