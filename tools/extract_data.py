# -*- coding: utf-8 -*-
"""武将・スキルの正本を「1件1ファイル」に切り出す(2026-08-13、作り直しの第1工程)。

■なぜ

これまでは characters.html(495KB) / characters-kyoku.html(467KB) / skills.html の
中にある巨大なJS配列が正本だった。1体直すのに1MB近いファイルを開くことになり、
差分も追いにくい。並列作業でも同じ行に触って衝突する。

これからは data/busho*/{No}.json ・ data/skill/{名前}.json が正本で、
一覧ページと個別ページはそこから組み立てる生成物にする。

■どうやって値を取るか

**JSの配列をPythonで再パースしない。** ページ自身をヘッドレスブラウザで開き、
その場で JSON.stringify して取り出す。表示に使われているのと同一の値になるので、
「パーサの解釈違いで値がずれる」事故が起きない(prerender.py と同じ考え方)。

■コメントの扱い

配列の中の // コメントは出典・検証の経緯を残した貴重な記録なので捨てない。
JSONにはコメントが書けないので、**データとして持たせる**:

  - 要素の直下にあるコメント          → その要素の "notes": [...]
  - 入れ子の配列の中で行の直前のもの   → その行の "note"

こうするとコメントが機械で読めるようになるので、監査から参照もできる。

■使い方

    python tools/extract_data.py           # 全部
    python tools/extract_data.py --check   # 書かずに照合だけ
"""
import collections
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (ページ, 配列名, 出力先, キーになるフィールド)
TARGETS = [
    ("characters.html", "generals", "data/busho", "no"),
    ("characters-kyoku.html", "kyokuGenerals", "data/busho-kyoku", "no"),
    # 2026-08-14: 極を「通常極」と「プラチナ極+シークレット極」に分けた。
    # 分け方はカードNo.の100の位(build_data.kyoku_type)。番号で決まるので
    # 置き場所も固定でよく、道具側は今までどおり1ページ=1フォルダで扱える。
    ("characters-kyoku-ps.html", "kyokuPsGenerals", "data/busho-kyoku-ps", "no"),
    ("characters-parallel.html", "parallelGenerals", "data/busho-parallel", "no"),
    ("characters-toku-s.html", "tokuSecretGenerals", "data/busho-toku-s", "no"),
    ("characters-toku.html", "tokuGenerals", "data/busho-toku", "no"),
    ("characters-ketsu.html", "ketsuGenerals", "data/busho-ketsu", "no"),
    ("skills.html", "skills", "data/skill", "name"),
]


def evaluate_array(page_name, array_name):
    """ページ自身のJSを実機で動かして配列をそのまま取り出す。"""
    from playwright.sync_api import sync_playwright
    url = "file:///" + os.path.join(ROOT, page_name).replace("\\", "/")
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto(url, wait_until="load")
        raw = pg.evaluate("JSON.stringify(%s)" % array_name)
        b.close()
    return json.loads(raw, object_pairs_hook=collections.OrderedDict)


def array_span(text, array_name):
    """`const X = [` の次から、対応する `]` の手前までの範囲を返す。"""
    m = re.search(r"\b(?:const|var|let)\s+%s\s*=\s*\[" % re.escape(array_name), text)
    if not m:
        raise SystemExit("配列 %s が見つからない" % array_name)
    return m.end(), match_bracket(text, m.end() - 1)


def match_bracket(text, i):
    """text[i] の括弧に対応する閉じ位置。文字列・コメントを飛ばす。"""
    open_ch = text[i]
    close_ch = {"[": "]", "{": "}"}[open_ch]
    depth = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in "'\"`":
            q = c
            i += 1
            while i < n and text[i] != q:
                i += 2 if text[i] == "\\" else 1
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            i = text.find("*/", i) + 1
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise SystemExit("括弧が閉じていない")


def top_level_objects(text, lo, hi):
    """配列の中身を、最上位の { ... } ごとに切り出す。"""
    out = []
    i = lo
    while i < hi:
        c = text[i]
        if c == "{":
            j = match_bracket(text, i)
            out.append((i, j + 1))
            i = j + 1
        elif c in "'\"`":
            q = c
            i += 1
            while i < hi and text[i] != q:
                i += 2 if text[i] == "\\" else 1
            i += 1
        elif c == "/" and text[i + 1:i + 2] == "/":
            i = text.find("\n", i)
            if i < 0:
                break
        else:
            i += 1
    return out


def strip_strings(line):
    """コメント判定のために、行から文字列リテラルを取り除く。"""
    return re.sub(r"'[^']*'|\"[^\"]*\"", "''", line)


def collect_comments(chunk):
    """1要素分のテキストから、コメントを「要素直下」と「行ごと」に振り分ける。

    入れ子の配列(synthesisTable / trTable / sourceCharacters 等)の中にあり、
    直後に行(= `{` で始まる要素)が続くコメントは、その行のものとして扱う。
    それ以外は要素全体の notes に入れる。
    """
    notes, row_notes = [], {}
    depth_arr = 0
    pending = []
    row_index = collections.Counter()
    cur_array = None
    for raw in chunk.split("\n"):
        line = raw.strip()
        bare = strip_strings(raw)
        m = re.match(r"//\s?(.*)$", line)
        if m:
            pending.append(m.group(1).rstrip())
            continue
        m2 = re.search(r"(\w+)\s*:\s*\[", bare)
        if m2 and depth_arr == 0:
            cur_array = m2.group(1)
        depth_arr += bare.count("[") - bare.count("]")
        if depth_arr <= 0:
            depth_arr = 0
            cur_array = None
        if pending:
            if cur_array and line.startswith("{"):
                row_notes.setdefault(cur_array, {})[row_index[cur_array]] = pending
            else:
                notes.extend(pending)
            pending = []
        if cur_array and line.startswith("{"):
            row_index[cur_array] += 1
    notes.extend(pending)
    return notes, row_notes


def merge(entry, notes, row_notes):
    if notes:
        entry["notes"] = notes
    for arr, per_row in row_notes.items():
        rows = entry.get(arr)
        if not isinstance(rows, list):
            continue
        for idx, text in per_row.items():
            if 0 <= idx < len(rows) and isinstance(rows[idx], dict):
                rows[idx]["note"] = text
    return entry


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|]', "_", s)


def guard(check_only, force):
    """既に data/ がある状態で走らせるのを止める(2026-08-13、実地で踏んだ)。

    この道具は**1回限りの引っ越し用**。正本が data/ に移ったあとに走らせると、
    ページ側(生成物)から data/ を作り直すことになり、逆流する。

    実際に踏んだ: 引っ越し後に何気なく走らせたら、notes が 255ファイル・555行
    消えた。build_data は要素直下の notes を要素の**前**にコメントとして書き出すが、
    extract 側は要素の `{...}` の中しか見ないので、拾い直せない。
    コミット前に気づいて git checkout で戻せたが、気づかなければ出典の記録が
    まとめて消えていた。

    往復で保つように直すより、**走らせないようにする**ほうが正しい。
    引っ越しはもう終わっているので、この道具の出番は原則もう無い。
    """
    if check_only or force:
        return
    live = [d for _p, _a, d, _k in TARGETS
            if os.path.isdir(os.path.join(ROOT, d))
            and any(f.endswith(".json") for f in os.listdir(os.path.join(ROOT, d)))]
    if live:
        print("[停止] 正本(data/)が既にある: %s" % ", ".join(live))
        print()
        print("この道具はページ内の配列から data/ を作る**引っ越し用**で、1回限りのもの。")
        print("いま走らせるとページ(生成物)から正本を作り直すことになり、逆流する。")
        print("要素直下の notes は build_data が要素の前に書き出すので拾い直せず、")
        print("出典の記録がまとめて消える(実地で 255ファイル・555行 消した)。")
        print()
        print("  data/ を直したい      → JSONを直接編集して python tools/build_data.py")
        print("  食い違いを見たい      → python tools/verify_extract.py")
        print("  それでも作り直したい  → --force(何が消えるか分かっている場合だけ)")
        raise SystemExit(1)


def run(check_only=False, force=False):
    guard(check_only, force)
    total, notes_total = 0, 0
    for page, array, outdir, keyfld in TARGETS:
        path = os.path.join(ROOT, page)
        if not os.path.exists(path):
            print("  %s は無いので飛ばす" % page)
            continue
        text = io.open(path, encoding="utf-8").read()
        data = evaluate_array(page, array)
        lo, hi = array_span(text, array)
        spans = top_level_objects(text, lo, hi)
        if len(spans) != len(data):
            raise SystemExit("%s: 実機 %d件 とテキスト %d件 が合わない"
                             % (page, len(data), len(spans)))
        d = os.path.join(ROOT, outdir)
        if not check_only and not os.path.isdir(d):
            os.makedirs(d)
        n_notes = 0
        for entry, (a, b) in zip(data, spans):
            notes, row_notes = collect_comments(text[a:b])
            n_notes += len(notes) + sum(len(v) for v in row_notes.values())
            merge(entry, notes, row_notes)
            key = safe_name(str(entry[keyfld]))
            if not check_only:
                with io.open(os.path.join(d, key + ".json"), "w",
                             encoding="utf-8", newline="\n") as f:
                    json.dump(entry, f, ensure_ascii=False, indent=1)
                    f.write("\n")
        print("  %-24s %3d件 → %s (コメント %d件)" % (page, len(data), outdir, n_notes))
        total += len(data)
        notes_total += n_notes
    print("合計 %d件 / 拾ったコメント %d件" % (total, notes_total))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    run(check_only="--check" in sys.argv, force="--force" in sys.argv)
