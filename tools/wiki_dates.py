# -*- coding: utf-8 -*-
"""各武将の「ixawiki のページに書かれた一番古いコメントの日付」を記録する。

**何のためか。** 章(ch)が極の79%で未確認のまま埋まらない。章はカードの
追加時期そのものなので、追加時期の代わりになる値があれば当たりが付く。
ixawiki のカードページの一番古いコメントは、そのカードが世に出た直後に
書かれていることが多く、実際 7002〜7009 が30章、7011〜7019 が31章と
日付順にきれいに並ぶ。

**サイトには出さない。** 書き込むのは data/busho*/{No}.json だけで、
build_data.py の LIST_FIELDS(白名簿)に足さないのでページには載らない。
あくまで章を推定するための内部の手がかりで、これ自体は事実の記録ではない。

  python tools/wiki_dates.py            # 記録されていない武将だけ
  python tools/wiki_dates.py --all      # 全部取り直す
  python tools/wiki_dates.py --roster   # **うちに無いカードも含めて** wiki 全件を
                                        # data/wiki_card_dates.json に書き出す

`--roster` は将来の登録用。いま登録していないカードも、いずれ登録する
可能性が高い(うぐさん)。そのときに調べ直さずに済むよう、wiki の名簿に
ある全カードの日付を1つのファイルに貯めておく。**こちらもサイトには出ない。**

■ ixawiki の URL の罠
`/wiki/index.php?` ではなく `/index.php?` で、クエリは **EUC-JP で**
URLエンコードする。UTF-8 で叩くと 200 が返るが本文は
「有効なWikiNameではありません」になる(2026-08-16 に一度これで
「サイトが落ちている」と誤読した)。
"""
import collections
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "tools", "wiki_cache")
DIRS = ("data/busho", "data/busho-kyoku", "data/busho-kyoku-ps", "data/busho-ketsu")
LISTS = ["Busho/武将カード一覧(天)", "Busho/武将カード一覧(極)",
         "Busho/武将カード一覧(特)"]
FIELD = "wikiOldestComment"
COUNT = "wikiCommentCount"
# **コメント行だけを拾う。** ページには右の「最終更新」欄や Last-modified など、
# コメントと無関係な日付が並んでいる。素の日付を拾うと、コメントが1件も無い
# ページで最近の日付を拾って「新しいカード」に見えてしまう。
# PukiWiki のコメントは必ず「-- 2024-08-11 (日) 20:53:02」の形で終わり、
# 日付は <span class="comment_date"> で囲まれている。**このクラス名で拾う。**
# 生HTMLに「-- 日付」の形で正規表現を当てても、間にタグが挟まるので当たらない
# (2026-08-16に358体すべて空振りした)。
COMMENT = re.compile(
    r'class="comment_date"[^>]*>\s*(20\d\d-\d\d-\d\d)\s*\([日月火水木金土]\)')
TAG = re.compile(r"[(（]([^)）]*)[)）]\s*$")


def fetch(page, use_cache=True):
    if not os.path.isdir(CACHE):
        os.makedirs(CACHE)
    key = "%s_%s.html" % (re.sub(r"[^0-9A-Za-z]", "_", page)[:40],
                          hashlib.sha1(page.encode("utf-8")).hexdigest()[:10])
    p = os.path.join(CACHE, key)
    if use_cache and os.path.exists(p):
        return io.open(p, encoding="utf-8").read()
    url = "https://ixawiki.com/index.php?" + urllib.parse.quote(page.encode("euc-jp"))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=60).read().decode("euc-jp", "replace")
    if "有効なWikiNameではありません" in raw:
        raise ValueError("ページが無い")
    io.open(p, "w", encoding="utf-8").write(raw)
    time.sleep(0.3)
    return raw


def wiki_names():
    """カードNo. → wiki 上の素の名前(ページ名を組み立てるのに要る)。"""
    out = {}
    for page in LISTS:
        try:
            raw = fetch(page)
        except Exception as e:
            print("  %s が取れない: %s" % (page, e))
            continue
        t = re.sub(r"(?i)</t[dh]>", " | ", raw)
        t = re.sub(r"(?i)</tr>", "\n", t)
        t = re.sub(r"<[^>]+>", "", t)
        t = re.sub(r"[ \t　]+", " ", t)
        for line in t.split("\n"):
            c = [x.strip() for x in line.strip(" |").split("|")]
            if len(c) > 2 and re.match(r"^\d+$", c[0]):
                m = TAG.search(c[2])
                base = (c[2][:m.start()] if m else c[2]).strip()
                if base and not base.endswith("?"):
                    out[c[0]] = base
    return out


def strip_deco(nm):
    s = re.sub(r"【[^】]*】", "", nm or "")
    s = re.sub(r"[-－]復刻[-－]", "", s)
    s = re.sub(r"[(（]\d+[)）]", "", s)
    return s.strip()


def oldest(no, base):
    """(一番古いコメントの日付, コメント件数)。

    件数は当てになるかどうかの目安。**ここ2年ほどwikiの更新が落ちていて、
    新しいカードほどコメントが付くのが遅い/付かない**(うぐさん)。
    件数が0や1なら、日付を追加時期の代わりにするのは危うい。
    """
    # **コメントの本体は `コメント/BushoCard/{No}{名前}`(pcommentプラグイン)。**
    # カードページ本体は「最新の30件を表示しています」で打ち切られるので、
    # コメントが30を超える武将では最古が後ろにずれる(No.1204 明智光秀で44日、
    # 2026-08-16に担当Aが実測)。`BushoCard/…/コメント` というページは存在しない。
    # **見つかった最初のページで打ち切る。** 両方を回して同じカウンタに
    # 足していたので、カードページに再掲される直近分がまるごと二重に
    # 数えられていた(2026-08-16に担当Gが実測。全38件が過大、少ない側では
    # ちょうど2倍になっていた)。日付は min なので影響していなかった。
    for page in ("コメント/BushoCard/%s%s" % (no, base),
                 "BushoCard/%s%s" % (no, base)):
        try:
            raw = fetch(page)
        except Exception:
            continue
        ds = COMMENT.findall(raw)
        if ds:
            return min(ds), len(ds)
    return None, 0


def main(redo=False):
    sys.stdout.reconfigure(encoding="utf-8")
    names = wiki_names()
    print("wiki の名簿: %d枚" % len(names))
    got, miss, skip = 0, [], 0
    for d in DIRS:
        full = os.path.join(ROOT, d)
        if not os.path.isdir(full):
            continue
        for fn in sorted(os.listdir(full)):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(full, fn)
            j = json.load(io.open(p, encoding="utf-8"),
                          object_pairs_hook=collections.OrderedDict)
            no = fn[:-5]
            if not redo and FIELD in j:
                skip += 1
                continue
            base = names.get(no) or strip_deco(j.get("name"))
            d8, n = oldest(no, base)
            j[FIELD] = d8
            j[COUNT] = n
            io.open(p, "w", encoding="utf-8", newline="\n").write(
                json.dumps(j, ensure_ascii=False, indent=1) + "\n")
            if d8:
                got += 1
            else:
                miss.append("%s %s" % (no, j.get("name")))
    print("記録できた %d / 取れなかった %d / もう記録済み %d"
          % (got, len(miss), skip))
    for x in miss[:40]:
        print("  取れない: " + x)


ROSTER = os.path.join(ROOT, "data", "wiki_card_dates.json")


def roster(redo=False):
    """wiki の名簿にある全カードの日付を貯める(うちに無いカードも含む)。

    いま登録していないカードも、いずれ登録する可能性が高い(うぐさん)。
    そのときに調べ直さずに済むようにしておく。**サイトには出ない。**
    """
    sys.stdout.reconfigure(encoding="utf-8")
    names = wiki_names()
    print("wiki の名簿: %d枚" % len(names))
    old = {}
    if os.path.exists(ROSTER) and not redo:
        old = json.load(io.open(ROSTER, encoding="utf-8"))

    have = set()
    for d in DIRS:
        full = os.path.join(ROOT, d)
        if os.path.isdir(full):
            have |= {f[:-5] for f in os.listdir(full) if f.endswith(".json")}

    out = collections.OrderedDict()
    got = 0
    for no in sorted(names, key=lambda x: (len(x), x)):
        if no in old and old[no].get("first"):
            out[no] = old[no]
            continue
        first, n, last = oldest_and_last(no, names[no])
        out[no] = collections.OrderedDict([
            ("name", names[no]), ("first", first), ("last", last),
            ("count", n), ("registered", no in have)])
        if first:
            got += 1
    io.open(ROSTER, "w", encoding="utf-8", newline="\n").write(
        json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    miss = [k for k, v in out.items() if not v.get("first")]
    print("%d枚を書いた(今回 %d枚を新しく取得) / 日付が取れない %d枚"
          % (len(out), got, len(miss)))
    print("うち、まだ登録していないカード %d枚"
          % sum(1 for v in out.values() if not v["registered"]))


def oldest_and_last(no, base):
    """(一番古いコメント, 件数, 一番新しいコメント)。"""
    # oldest() と同じく、見つかった最初のページで打ち切る(二重計上を避ける)
    for page in ("コメント/BushoCard/%s%s" % (no, base),
                 "BushoCard/%s%s" % (no, base)):
        try:
            raw = fetch(page)
        except Exception:
            continue
        ds = COMMENT.findall(raw)
        if ds:
            return min(ds), len(ds), max(ds)
    return None, 0, None


if __name__ == "__main__":
    if "--roster" in sys.argv:
        roster("--all" in sys.argv)
    else:
        main("--all" in sys.argv)
