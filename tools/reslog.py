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
# CC-1(2026-08-13 第7回レッドチーム、高): evidence が dict で status が真なら
# 証拠として数えていたので、JSON に2行手で足すだけで「2ソースに当たった」ことに
# でき、HIGH が消えて緑で通った。取得本文の実体をここに残し、監査は実体から
# ハッシュと大きさを計算して突き合わせる。手書きでは実体を用意できない。
# (実体を偽造することは原理的には可能だが、その場合ページ本文まで捏造した
#  ファイルが差分に現れる。黙って2行足すのとは痕跡の重さが違う。)
CACHE = os.path.join(ROOT, "tools", "research_cache")
LEGACY = os.path.join(ROOT, "tools", "reslog_legacy.txt")
MIN_BYTES = 2000        # 実在のページでこれを下回ることはまず無い
KEEP_BYTES = 65536      # 保存は先頭64KBまで(照合はこの範囲で行う)


def _legacy():
    """キャッシュ実体を持たない旧形式を、例外として認める組み合わせ。

    「日付が古ければ通す」にすると、日付を書くだけで偽造が復活する。
    名指しの一覧にして、錠前で守る。
    """
    out = set()
    if not os.path.exists(LEGACY):
        return out
    for line in io.open(LEGACY, encoding="utf-8").read().split("\n"):
        if line.startswith("#") or "\t" not in line:
            continue
        k, _, src = line.partition("\t")
        out.add((k.strip(), src.strip()))
    return out


def cache_name(key, source):
    import hashlib
    h = hashlib.sha256((key + "|" + source).encode("utf-8")).hexdigest()[:16]
    return h + ".txt"



def _load():
    if not os.path.exists(PATH):
        return {}
    with io.open(PATH, encoding="utf-8") as f:
        return json.load(f)


def _save(d):
    with io.open(PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)


def _write(key, source, url, result, found, evidence):
    d = _load()
    entries = [e for e in d.get(key, []) if e["source"] != source]
    entries.append({"source": source, "url": url, "result": result,
                    "found": bool(found), "evidence": evidence,
                    "date": datetime.date.today().isoformat()})
    d[key] = sorted(entries, key=lambda e: e["source"])
    _save(d)


def log(key, source, url, result, found):
    """手書きの記録。証拠が無いので evidence:"manual" として残る。
    監査は証拠つき(fetch_and_log)の件数だけを2ソース判定に数える。"""
    _write(key, source, url, result, found, "manual")


def fetch_and_log(key, source, url, encoding="utf-8", judge=None):
    """実際に取得してから記録する。手書きで済ませられないようにするための入口。

    A-9(2026-08-12レッドチーム指摘)への対応。log() は文字列を受け取るだけなので、
    ワンライナーで「見たことにする」のを止められなかった。こちらは自分でHTTPを叩き、
    ステータス・本文長・本文のSHA256を証拠として残す。

    judge(text) -> (result:str, found:bool) を渡すと、取得本文から判定まで行う。
    """
    import hashlib
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            status = r.getcode()
    except Exception as e:
        _write(key, source, url, "取得失敗: %s" % e, False,
               evidence={"status": None, "bytes": 0, "sha256": None})
        return None
    text = raw.decode(encoding, "replace")
    result, found = judge(text) if judge else ("取得した(%d bytes)" % len(raw), True)
    body = raw[:KEEP_BYTES]
    if not os.path.isdir(CACHE):
        os.makedirs(CACHE)
    name = cache_name(key, source)
    with open(os.path.join(CACHE, name), "wb") as f:
        f.write(body)
    _write(key, source, url, result, found,
           evidence={"status": status, "bytes": len(raw),
                     "sha256": hashlib.sha256(raw).hexdigest()[:16],
                     "cache": name,
                     "cache_sha256": hashlib.sha256(body).hexdigest()[:16],
                     "cache_bytes": len(body)})
    return text


def sources(key, verified_only=False):
    """その項目で当たった情報源の一覧。
    verified_only=True なら fetch_and_log で取得した(証拠つきの)ものだけ返す。"""
    import hashlib
    out = []
    for e in _load().get(key, []):
        ev = e.get("evidence")
        if verified_only:
            if not (isinstance(ev, dict) and ev.get("status")):
                continue
            # CC-1: 自己申告の数字ではなく、保存された本文の実体で確かめる。
            name = ev.get("cache")
            if not name:
                if (key, e.get("source")) in _legacy():
                    out.append(e["source"])   # 名指しの旧形式だけ例外
                continue
            p = os.path.join(CACHE, os.path.basename(str(name)))
            if not os.path.exists(p):
                continue
            with open(p, "rb") as f:
                body = f.read()
            if len(body) < MIN_BYTES:
                continue
            if ev.get("cache_bytes") != len(body):
                continue
            if ev.get("cache_sha256") != hashlib.sha256(body).hexdigest()[:16]:
                continue
            if name != cache_name(key, e.get("source", "")):
                continue        # 別項目のキャッシュの使い回しを防ぐ
        out.append(e["source"])
    return out


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
