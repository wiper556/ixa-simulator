# -*- coding: utf-8 -*-
"""期間限定くじの入れ替えを1コマンドで終わらせる。

なぜ要るか(2026-09-02):
くじは2週間〜1か月ごとに入れ替わる。前回の入れ替えは手作業で、
**画像から書き写した数値を Python の辞書に起こし、HTMLの5箇所を手で直し、
アーカイブページを手で組み立てる**という流れで何時間もかかった。
やることは毎回同じなので道具にする。

    python tools/gacha_update.py --check
        いまのページを検算するだけ(種数・合計・武将名が正本と合っているか)

    python tools/gacha_update.py --lineup _work/kuji.txt \
        --end "2026年9月1日14:00" --start "2026年9月1日17:00" --name "紅蓮の拾六文銭"
        前の期間をアーカイブし、新しい内容に切り替える。--dry で下見だけ。

────────────────────────────────────────────────────────
ラインナップの書き方(_work/kuji.txt)
────────────────────────────────────────────────────────
ゲーム内「確率情報」の画面を見ながら、**割合ごとにまとめて**書く。
1枚ずつ書かなくてよいので、65種でも8行で済む。

    # 行頭が # の行と空行は読み飛ばす
    [救済/極]
    1.3645: 2336 2358 2359 2366 2368 2374-2399 7001-7021
    1.3646: 7022
    1.8000: 2640
    0.8333: 2857 2858 7405 7406
    0.8334: 2859 7407
    2.7291: 7023 7024
    4.0936: 7025 7026
    0.2000: 32640

  ・パターンは ベース / ブースト / 救済 の3つ
      ベース   = 単発 と 10連1〜9枚目
      ブースト = 10連10枚目(9枚目までに極以上が1枚以上出ている場合)
      救済     = 10連10枚目(9枚目までに極以上が1枚も出ていない場合)
  ・レアリティは 傑 天 極 特 上
  ・No. は「2374-2399」のように範囲で書ける。**正本にある番号だけ拾う**ので
    欠番を気にしなくてよい。
  ・**書かなかったパターン/レアリティは前の期間のまま。** 変わった所だけ書けばよい。
  ・武将名は書かない。正本(data/busho*/)から自動で入る(監査 S-18 対策)。

レア度ごとの割合そのものが変わったときは、こう書く:

    [割合/ベース]
    傑 0.018
    天 2.000
    極 15.000
    特 36.500
    上 46.482

────────────────────────────────────────────────────────
必ず確かめること(道具が自動でやる)
────────────────────────────────────────────────────────
  ・レア度ごとの内訳の合計が、そのレア度の割合と一致するか
  ・パターンごとの総合計が 100.000% になるか
  ・書いた No. が正本にあるか(無ければ止める)
  ・前の期間から何が増えて何が減ったかを出す(目視確認用)
どれか1つでも合わなければ **何も書かずに止まる。**
"""
import argparse
import glob
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, "gacha-simulator.html")
NL = chr(10)

# パターン名 → (レアリティ→配列名, 割合定数名)
PATTERNS = {
    "ベース": ({"傑": "KETSU_CHARS", "天": "TEN_CHARS", "極": "KYOKU_CHARS",
              "特": "TOKU_CHARS", "上": "JOU_CHARS"}, "BASE_RATES"),
    "ブースト": ({"傑": "KETSU_CHARS_BOOST", "天": "TEN_CHARS_BOOST",
               "極": "KYOKU_CHARS_BOOST", "特": "TOKU_CHARS_BOOST",
               "上": "JOU_CHARS_BOOST"}, "TENTH_BOOST_RATES"),
    # 救済は傑と天をブーストと共有している(ページ側の TIER_CHAR_LISTS_GUARANTEE がそう)
    "救済": ({"傑": "KETSU_CHARS_BOOST", "天": "TEN_CHARS_BOOST",
            "極": "KYOKU_CHARS_GUARANTEE"}, "TENTH_GUARANTEE_RATES"),
}
TIERS = ("傑", "天", "極", "特", "上")


def canon_names():
    out = {}
    for f in glob.glob(os.path.join(ROOT, "data", "busho*", "*.json")):
        j = json.load(io.open(f, encoding="utf-8"))
        out[str(j["no"])] = j["name"]
    return out


def read_page():
    return io.open(PAGE, encoding="utf-8", newline="").read()


def get_array(text, name):
    """配列を {No: (名前, 割合)} で返す。"""
    m = re.search(r"const " + name + r" = \[(.*?)\n  \];", text, re.S)
    if not m:
        raise SystemExit("配列 %s が見つからない" % name)
    out = {}
    for no, nm, w in re.findall(r"\{no:(\d+), name:'([^']*)', w:([\d.]+)\}", m.group(1)):
        out[no] = (nm, w)
    return out


def get_rates(text, name):
    m = re.search(r"const " + name + r" = \{([^}]*)\}", text)
    if not m:
        raise SystemExit("割合 %s が見つからない" % name)
    return {k: int(v) for k, v in re.findall(r"([傑天極特上])\s*:\s*(\d+)", m.group(1))}


# ---------------------------------------------------------------- 検算
def check(text, quiet=False):
    """いまのページを検算する。おかしければ理由を並べて返す。"""
    bad = []
    names = canon_names()
    for pat, (arrs, ratename) in PATTERNS.items():
        rates = get_rates(text, ratename)
        total = 0.0
        count = 0
        for tier, arr in arrs.items():
            rows = get_array(text, arr)
            s = sum(float(w) for _n, w in rows.values())
            total += s
            count += len(rows)
            want = rates.get(tier, 0) / 1000.0
            if abs(s - want) > 0.0005:
                bad.append("%s の %s: 内訳の合計 %.4f%% が割合 %.3f%% と合わない"
                           % (pat, tier, s, want))
            for no, (nm, _w) in rows.items():
                if no in names and names[no] != nm:
                    bad.append("%s の %s No.%s: くじ「%s」/ 正本「%s」"
                               % (pat, tier, no, nm, names[no]))
                elif no not in names:
                    bad.append("%s の %s No.%s「%s」が正本に無い" % (pat, tier, no, nm))
        # 救済は傑と天をブーストと共有するので、割合の欄に無いレアは 0 として合計に効かない
        if abs(total - 100.0) > 0.0015:
            bad.append("%s の総合計が %.4f%%(100%% でない)" % (pat, total))
        if not quiet:
            print("  %-6s %3d種  合計 %.4f%%" % (pat, count, total))
    return bad


# ---------------------------------------------------------------- 入力の読み取り
def parse_lineup(path, names):
    """[パターン/レアリティ] と「割合: No…」を読む。

    返り値: ({(パターン,レア): {No: 割合}}, {パターン: {レア: 千分率}})
    """
    lines = io.open(path, encoding="utf-8").read().split(NL)
    body, rates, cur, mode = {}, {}, None, None
    for ln, raw in enumerate(lines, 1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"^\[(割合)/(\S+)\]$", s)
        if m:
            mode, cur = "rate", m.group(2)
            if cur not in PATTERNS:
                raise SystemExit("%d行目: パターン名「%s」は ベース/ブースト/救済 のどれか"
                                 % (ln, cur))
            rates.setdefault(cur, {})
            continue
        m = re.match(r"^\[(\S+)/(\S+)\]$", s)
        if m:
            pat, tier = m.group(1), m.group(2)
            if pat not in PATTERNS:
                raise SystemExit("%d行目: パターン名「%s」は ベース/ブースト/救済 のどれか"
                                 % (ln, pat))
            if tier not in PATTERNS[pat][0]:
                raise SystemExit("%d行目: %s に レアリティ「%s」は無い" % (ln, pat, tier))
            mode, cur = "body", (pat, tier)
            body.setdefault(cur, {})
            continue
        if mode == "rate":
            m = re.match(r"^([傑天極特上])\s+([\d.]+)$", s)
            if not m:
                raise SystemExit("%d行目: 「傑 0.018」の形で書く: %s" % (ln, s))
            rates[cur][m.group(1)] = int(round(float(m.group(2)) * 1000))
            continue
        if mode != "body":
            raise SystemExit("%d行目: 先に [パターン/レアリティ] を書く: %s" % (ln, s))
        m = re.match(r"^([\d.]+)\s*[:：]\s*(.+)$", s)
        if not m:
            raise SystemExit("%d行目: 「0.4098: 2336 2358 …」の形で書く: %s" % (ln, s))
        w = m.group(1)
        for tok in m.group(2).split():
            r = re.match(r"^(\d+)-(\d+)$", tok)
            if r:
                lo, hi = int(r.group(1)), int(r.group(2))
                if hi < lo:
                    raise SystemExit("%d行目: 範囲が逆さま: %s" % (ln, tok))
                got = [str(n) for n in range(lo, hi + 1) if str(n) in names]
                if not got:
                    raise SystemExit("%d行目: 範囲 %s に正本の武将が1人も居ない" % (ln, tok))
                for n in got:
                    body[cur][n] = w
            else:
                if not tok.isdigit():
                    raise SystemExit("%d行目: No. として読めない: %s" % (ln, tok))
                if tok not in names:
                    raise SystemExit("%d行目: No.%s が正本に無い。先に武将を登録する" % (ln, tok))
                body[cur][tok] = w
    return body, rates


def build_array(name, rows, names):
    out = ["  const %s = [" % name]
    keys = sorted(rows, key=lambda x: (len(x), x))
    for i, no in enumerate(keys):
        out.append("    {no:%s, name:'%s', w:%s}%s"
                   % (no, names[no], rows[no], "," if i < len(keys) - 1 else ""))
    out.append("  ];")
    return NL.join(out)


# ---------------------------------------------------------------- 書き換え
def replace_once(text, old, new, what):
    if text.count(old) != 1:
        raise SystemExit("%s が %d箇所見つかった(1箇所であるべき)" % (what, text.count(old)))
    return text.replace(old, new)


def do_archive(text, end, start_label, dry):
    """いまの内容を記録用ページとして固定する。ファイル名は開始日から作る。"""
    m = re.search(r'<p class="period-banner">開催期間: ([^<]*?)\s*〜[^<]*</p>', text)
    if not m:
        raise SystemExit("開催期間の帯が読めない")
    cur_start = m.group(1).strip()
    d = re.match(r"(\d+)年(\d+)月(\d+)日", cur_start)
    if not d:
        raise SystemExit("開催期間「%s」から日付を取れない" % cur_start)
    fn = "gacha-kuji-%04d-%02d-%02d.html" % tuple(int(x) for x in d.groups())
    path = os.path.join(ROOT, fn)
    if os.path.exists(path):
        raise SystemExit("%s は既にある" % fn)
    label = "%s 〜 %s" % (cur_start, end)

    a = text
    i, j = a.index("<!--"), a.index("-->") + 3
    a = (a[:i] + "<!--" + NL
         + "  【このファイルについて】" + NL
         + "  期間限定くじ「%s」の内容を固定で再現した記録用ページ。" % label + NL
         + "  gacha-simulator.html が次の期間のルールに切り替わった後も、当時の内容を" + NL
         + "  そのまま遊べるように残している。" + NL
         + "  gacha-simulator.html の「過去のくじ」セクションからリンクしている。" + NL
         + "  **内容を変更しないこと**(当時の記録としての価値が失われるため)。" + NL + NL
         + "  この固定は tools/gacha_update.py が作った。" + NL
         + "-->" + a[j:])
    a = replace_once(a, m.group(0),
                     '<p class="period-banner">開催期間: %s(終了)</p>' % label,
                     "開催期間の帯")
    k = re.search(r'\n    <div class="content-block">\s*\n      <h2>過去のくじ</h2>.*?\n    </div>\n',
                  a, re.S)
    if not k:
        raise SystemExit("「過去のくじ」の節が見つからない")
    a = a[:k.start()] + NL + a[k.end():]
    if not dry:
        io.open(path, "w", encoding="utf-8", newline=NL).write(a)
    print("  記録用ページ: %s 「%s」%s" % (fn, label, "(下見)" if dry else ""))

    # 本体側にリンクを足す
    text = replace_once(
        text, '      <ul class="past-kuji-list">',
        '      <ul class="past-kuji-list">' + NL
        + '        <li><a href="%s">%s</a></li>' % (fn, label),
        "過去のくじの一覧")
    m2 = re.search(r"^    - gacha-kuji-[0-9-]+\.html .*$", text, re.M)
    if m2:
        text = text.replace(m2.group(0),
                            "    - %s … %s" % (fn, label) + NL + m2.group(0), 1)
    return text, fn, label


def main(argv):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--check", action="store_true", help="いまのページを検算するだけ")
    ap.add_argument("--lineup", help="ラインナップを書いたファイル")
    ap.add_argument("--end", help="前の期間の終わり(例: 2026年9月1日14:00)")
    ap.add_argument("--start", help="新しい期間の始まり(例: 2026年9月1日17:00)")
    ap.add_argument("--name", default="", help="新しいくじの名前(例: 紅蓮の拾六文銭)")
    ap.add_argument("--version", help="GACHA_VERSION(省略すると日付から作る)")
    ap.add_argument("--dry", action="store_true", help="書かずに下見だけ")
    a = ap.parse_args(argv)

    text = read_page()
    names = canon_names()

    if a.check or not a.lineup:
        print("いまのページの検算:")
        bad = check(text)
        print()
        if bad:
            print("★おかしいところ %d件:" % len(bad))
            for b in bad[:20]:
                print("   " + b)
            return 1
        print("問題なし。")
        return 0

    for need in ("end", "start"):
        if not getattr(a, need):
            raise SystemExit("--%s が要る" % need)

    body, rates = parse_lineup(a.lineup, names)
    print("読み取ったラインナップ:")
    for (pat, tier), rows in sorted(body.items()):
        print("  [%s/%s] %d種" % (pat, tier, len(rows)))

    # ---- 新しい中身を組み立てて検算する(まだ書かない) ----
    new = text
    for pat, tiers in rates.items():
        rn = PATTERNS[pat][1]
        cur = get_rates(new, rn)
        cur.update(tiers)
        body_s = ", ".join("%s:%d" % (t, cur[t]) for t in TIERS if t in cur)
        new = re.sub(r"const " + rn + r" = \{[^}]*\}",
                     "const %s = {%s}" % (rn, body_s), new)
        print("  割合を書き換え: %s → %s" % (rn, body_s))
    changed = []
    for (pat, tier), rows in body.items():
        arr = PATTERNS[pat][0][tier]
        before = get_array(new, arr)
        new = re.sub(r"  const " + arr + r" = \[.*?\n  \];",
                     lambda _m: build_array(arr, rows, names), new, flags=re.S)
        added = sorted(set(rows) - set(before), key=lambda x: (len(x), x))
        gone = sorted(set(before) - set(rows), key=lambda x: (len(x), x))
        moved = [n for n in set(rows) & set(before) if before[n][1] != rows[n]]
        changed.append((pat, tier, arr, len(before), len(rows), added, gone, len(moved)))

    print()
    print("前の期間からの変化:")
    for pat, tier, arr, n0, n1, added, gone, moved in changed:
        print("  [%s/%s] %d種 → %d種" % (pat, tier, n0, n1))
        if gone:
            print("     外れた: " + "・".join("No.%s %s" % (n, names[n]) for n in gone))
        if added:
            print("     入った: " + "・".join("No.%s %s" % (n, names[n]) for n in added))
        if moved:
            print("     割合が変わった: %d種" % moved)

    print()
    print("検算:")
    bad = check(new)
    if bad:
        print()
        print("★合わないので **何も書かずに止めた**。%d件:" % len(bad))
        for b in bad[:20]:
            print("   " + b)
        return 1

    # ---- ここまで通ったら、アーカイブと切り替えを行う ----
    #
    # **記録用ページは切り替え『前』の text から作る。** new(切り替え後)から作ると
    # 当時の内容ではなく新しい内容が固定されてしまう。
    # 2026-09-02、手作業の結果と突き合わせて見つけた誤り。
    print()
    old_archived, fn, label = do_archive(text, a.end, a.start, a.dry)
    # 本体側に足された「過去のくじ」へのリンクだけを new に写す
    new = replace_once(
        new, '      <ul class="past-kuji-list">',
        '      <ul class="past-kuji-list">' + NL
        + '        <li><a href="%s">%s</a></li>' % (fn, label),
        "過去のくじの一覧")
    m2 = re.search(r"^    - gacha-kuji-[0-9-]+\.html .*$", new, re.M)
    if m2:
        new = new.replace(m2.group(0),
                          "    - %s … %s" % (fn, label) + NL + m2.group(0), 1)
    del old_archived
    period = "%s 〜%s" % (a.start, ("　" + a.name) if a.name else "")
    new = replace_once(new,
                       re.search(r'<p class="period-banner">[^<]*</p>', new).group(0),
                       '<p class="period-banner">開催期間: %s</p>' % period,
                       "開催期間の帯")
    d = re.match(r"(\d+)年(\d+)月(\d+)日", a.start)
    scope = "%04d-%02d-%02d" % tuple(int(x) for x in d.groups())
    new = re.sub(r"const KUJI_SCOPE = '[^']*';",
                 "const KUJI_SCOPE = '%s';" % scope, new)
    ver = a.version or ("v%s.1" % scope.replace("-", "-"))
    new = re.sub(r"const GACHA_VERSION = '[^']*';",
                 "const GACHA_VERSION = '%s';" % ver, new)
    m = re.search(r"^  現在のくじルール\(排出確率・武将データ等\)は .*$", new, re.M)
    if m:
        new = new.replace(m.group(0),
                          "  現在のくじルール(排出確率・武将データ等)は %s のくじの内容。"
                          % period.replace(" 〜", "〜"))
    print("  開催期間: %s" % period)
    print("  KUJI_SCOPE: %s / GACHA_VERSION: %s" % (scope, ver))

    if a.dry:
        print()
        print("(--dry なので何も書いていない)")
        return 0
    io.open(PAGE, "w", encoding="utf-8", newline=NL).write(new)
    print()
    print("書き出した。このあと:")
    print("  python tools/build_data.py && python tools/prerender.py"
          " && python tools/gen_detail_pages.py")
    print("  python tools/audit_characters.py   (S-18/S-23 が確率と名前を見る)")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
