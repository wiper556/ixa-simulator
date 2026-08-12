# -*- coding: utf-8 -*-
"""調査ログ。「どこを見て、何が分かったか」を消えない形で残す。

なぜ要るか(docs/RULE-OPERATION.md の穴1):
「未確認」と書くとき、本当に情報源に当たったのかを機械で確かめる手段が無かった。
2026-08-12、ixanaryにデータがあるのに調べずに「未確認」と表示した違反(I-01)が起きている。
口約束の代わりに、当たった先をここに記録し、監査から参照できるようにする。

キャッシュ(%TEMP%)は消えるのでリポジトリに置く。手で書かずスクリプトから追記する。

    from tools.reslog import log
    log("TR:白銀双鶴", "ixanary", url, "TR1〜4のセルが空", found=False)

    python tools/reslog.py --show TR:白銀双鶴   # 何を見たか確認
"""
import datetime
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "research_log.json")


def _load():
    if not os.path.exists(PATH):
        return {}
    with io.open(PATH, encoding="utf-8") as f:
        return json.load(f)


def _save(d):
    with io.open(PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)


def log(key, source, url, result, found):
    """1回の確認を記録する。同じ key×source は上書き(最後に見た結果を残す)。"""
    d = _load()
    entries = [e for e in d.get(key, []) if e["source"] != source]
    entries.append({"source": source, "url": url, "result": result,
                    "found": bool(found),
                    "date": datetime.date.today().isoformat()})
    d[key] = sorted(entries, key=lambda e: e["source"])
    _save(d)


def sources(key):
    """その項目で当たった情報源の一覧。"""
    return [e["source"] for e in _load().get(key, [])]


def checked(key, minimum=2):
    """最低 minimum 件の情報源に当たっているか。"""
    return len(sources(key)) >= minimum


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    d = _load()
    if "--show" in sys.argv:
        k = sys.argv[sys.argv.index("--show") + 1]
        for e in d.get(k, []):
            print("  %-10s %-6s %s  %s" % (e["source"], "見つかった" if e["found"] else "無し",
                                           e["date"], e["result"]))
        if k not in d:
            print("  記録なし")
    else:
        print("調査ログ: %d項目" % len(d))
        for k in sorted(d):
            src = "/".join(e["source"] for e in d[k])
            got = any(e["found"] for e in d[k])
            print("  %-28s %-24s %s" % (k, src, "取得済み" if got else "どこにも無し"))
