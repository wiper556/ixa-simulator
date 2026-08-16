# -*- coding: utf-8 -*-
"""wikiの最古コメント日から章を当てて、確定済みの章とどれだけ合うかを測る。

章の期間はうぐさんの提供(2026-08-16)。**（N）と違って章はゲーム側の
本物の区切り**なので、これが正本。

**章末の約1週間は次章のカード情報が先に出る。** 例えば 2024-02-02 に
ページが作られていたら、26章(〜2024-02-08)ではなく27章(2024-02-09〜)の
可能性が高い(うぐさん)。そこで、章の終わりから7日以内に入る日付は
「その章か次章か決められない」として保留にする。

  python tools/chapter_guess.py           # 精度を測るだけ
  python tools/chapter_guess.py --write   # chGuess に書き込む
"""
import collections
import datetime
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRS = ("data/busho", "data/busho-kyoku", "data/busho-kyoku-ps", "data/busho-ketsu")
FIELD = "chGuess"
# (章, 開始日, 終了日)。うぐさん提供(2026-08-16)。
#
# **1章と2章の境目は分からない。** うぐさん自身も特定できていないので、
# 2011-09-13 以前は「1章か2章」としてまとめる。
#
# **2011年前半より古いコメントは wiki に残っていない。**
# 東日本大震災のあとにデータを軽くするため過去ログが消されており、
# 実際、最古コメントの分布は 2011-03 でぷつりと切れて 2010年以前が0枚
# (2011-03=13枚 / 04=12 / 05=24 / 06=21 …)。
# つまり **1〜3章のカードは「いつ実装されたか」を最古コメントから
# 復元できない**。EARLY_CUT より前の日付は章を出さない。
EARLY_CUT = "2011-09-14"      # これより前は 1章か2章か3章か判別できない
CHAPTERS = [
    (1, "2010-08-02", "2011-09-13"),      # 1章と2章の境目は不明
    (3, "2011-09-14", "2012-04-16"), (4, "2012-04-17", "2012-10-17"),
    (5, "2012-10-18", "2013-05-16"), (6, "2013-05-17", "2013-11-11"),
    (7, "2013-11-12", "2014-05-16"), (8, "2014-05-17", "2014-11-18"),
    (9, "2014-11-19", "2015-05-10"), (10, "2015-05-19", "2015-11-13"),
    (11, "2015-11-14", "2016-05-18"), (12, "2016-05-19", "2016-11-14"),
    (13, "2016-11-15", "2017-05-18"), (14, "2017-05-19", "2018-01-14"),
    (15, "2018-01-15", "2018-08-09"),
    (16, "2018-08-10", "2019-02-08"), (17, "2019-02-09", "2019-08-07"),
    (18, "2019-08-08", "2020-02-13"), (19, "2020-02-14", "2020-08-13"),
    (20, "2020-08-14", "2021-02-04"), (21, "2021-02-05", "2021-08-12"),
    (22, "2021-08-13", "2022-02-13"), (23, "2022-02-14", "2022-08-05"),
    (24, "2022-08-05", "2023-02-02"), (25, "2023-02-03", "2023-08-04"),
    (26, "2023-08-11", "2024-02-08"), (27, "2024-02-09", "2024-08-08"),
    (28, "2024-08-09", "2025-02-06"), (29, "2025-02-07", "2025-08-07"),
    (30, "2025-08-08", "2026-02-05"), (31, "2026-02-06", "2026-08-06"),
    (32, "2026-08-07", "2099-12-31"),
]
EARLY = 10         # 章末この日数は次章の情報が先に出ている可能性がある
# うぐさんは「1週間くらい」と言ったが、23章の天12体が章開始(2022-02-14)の
# 9〜10日前(2022-02-04/05)に揃っているので10日にした。


def guess(d):
    """(章, 迷っている次章 or None)。範囲外なら (None, None)。

    2011年前半までのコメントは震災後に消されているので、そこに落ちる
    日付は「本当に古いカード」なのか「古いログが消えて残った最古が
    たまたまその日」なのか区別できない。章を出さない。
    """
    if d < EARLY_CUT:
        return None, None
    for i, (ch, lo, hi) in enumerate(CHAPTERS):
        if lo <= d <= hi:
            end = datetime.date(*map(int, hi.split("-")))
            day = datetime.date(*map(int, d.split("-")))
            nxt = CHAPTERS[i + 1][0] if i + 1 < len(CHAPTERS) else None
            if nxt and (end - day).days < EARLY:
                return ch, nxt
            return ch, None
    return None, None


def load():
    out = []
    for d in DIRS:
        p = os.path.join(ROOT, d)
        if not os.path.isdir(p):
            continue
        for fn in sorted(os.listdir(p)):
            if fn.endswith(".json"):
                out.append((os.path.join(p, fn), fn[:-5]))
    return out


def main(write=False):
    sys.stdout.reconfigure(encoding="utf-8")
    hit, miss, near, unknown, wrote = 0, [], 0, 0, 0
    fill = collections.Counter()
    for path, no in load():
        j = json.load(io.open(path, encoding="utf-8"),
                      object_pairs_hook=collections.OrderedDict)
        d = j.get("wikiOldestComment")
        ch = j.get("ch")
        g, nxt = guess(d) if d else (None, None)
        known = int(ch.replace("章", "")) if ch and ch != "未確認" else None

        if known is not None and g is not None:
            if g == known or nxt == known:
                hit += 1
                if nxt == known and g != known:
                    near += 1
            else:
                miss.append((no, j.get("name"), known, g, d,
                             j.get("wikiCommentCount")))
        if known is None:
            if g is None:
                unknown += 1
            else:
                fill[(g, nxt)] += 1

        if write:
            v = None if g is None else ("%d章" % g if not nxt
                                        else "%d章か%d章" % (g, nxt))
            if j.get(FIELD) != v:
                j[FIELD] = v
                io.open(path, "w", encoding="utf-8", newline="\n").write(
                    json.dumps(j, ensure_ascii=False, indent=1) + "\n")
                wrote += 1

    tot = hit + len(miss)
    print("章が分かっていて日付もある: %d体" % tot)
    if tot:
        print("  当たった: %d体 (%.1f%%)  うち「章末1週間で次章」で救われた %d体"
              % (hit, 100.0 * hit / tot, near))
    print("  外した: %d体" % len(miss))
    for no, name, known, g, d, c in sorted(miss, key=lambda x: x[4]):
        print("    No.%-6s %-20s 登録=%d章 / %s → %s章  コメント%s件"
              % (no, name, known, d, g, c))

    print("\n章が未確認のもの")
    for (g, nxt), n in sorted(fill.items(), key=lambda x: (x[0][0], x[0][1] or 0)):
        print("  %-10s %d体" % ("%d章" % g if not nxt else "%d章か%d章" % (g, nxt), n))
    print("  日付が無くて判定できない: %d体" % unknown)
    if write:
        print("\n%s に %d体を書いた" % (FIELD, wrote))


ROSTER = os.path.join(ROOT, "data", "wiki_card_dates.json")


def annotate_roster():
    """未登録カードの名簿にも章の当たりを付ける。

    いま登録していないカードもいずれ登録する(うぐさん)。そのときに
    章を調べ直さずに済むよう、名簿の側にも同じ判定を入れておく。
    """
    if not os.path.exists(ROSTER):
        print("  名簿がまだ無い: %s" % ROSTER)
        return
    j = json.load(io.open(ROSTER, encoding="utf-8"),
                  object_pairs_hook=collections.OrderedDict)
    n = collections.Counter()
    for no, v in j.items():
        g, nxt = guess(v.get("first")) if v.get("first") else (None, None)
        v["chGuess"] = (None if g is None
                        else ("%d章" % g if not nxt else "%d章か%d章" % (g, nxt)))
        n["付いた" if g else "付かない"] += 1
        n["未登録で付いた" if (g and not v.get("registered")) else "-"] += 1
    io.open(ROSTER, "w", encoding="utf-8", newline="\n").write(
        json.dumps(j, ensure_ascii=False, indent=1) + "\n")
    print("名簿 %d枚: 章が付いた %d / 付かない %d (うち未登録カードで付いた %d)"
          % (len(j), n["付いた"], n["付かない"], n["未登録で付いた"]))


if __name__ == "__main__":
    if "--roster" in sys.argv:
        sys.stdout.reconfigure(encoding="utf-8")
        annotate_roster()
    else:
        main("--write" in sys.argv)
