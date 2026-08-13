# -*- coding: utf-8 -*-
"""data/ の1件1JSONから、各ページのデータ配列を組み立てて書き戻す(作り直しの第2工程)。

■役割

正本は data/busho*/{No}.json と data/skill/{名前}.json。
このスクリプトがそれを読んで、characters*.html / skills.html の中の配列を
**生成ブロック**として差し替える。以後、配列は手で編集しない。

    正本  data/busho-kyoku/2398.json     ← ここだけ直す
      ↓  tools/build_data.py
    配列  characters-kyoku.html          ← 生成物
      ↓  tools/prerender.py / tools/gen_detail_pages.py
    表示  busho/2398.html ほか            ← 生成物

■なぜ配列を消してJSONにしないのか

ページの描画JS・監査・フック・自己テストがどれも今の配列の書き方を前提に
できている。書き方まで同時に変えると、どこが壊れたのか切り分けられなくなる。
**出力は今までと同じJSの書き方に揃えて、正本の置き場所だけを移す。**
表示も検査もそのまま通ることを確かめてから、次の段へ進む。

■コメント

JSON側の "notes"(要素全体) と 行の "note" を、// のコメント行として書き戻す。
データとしては出力しない(ページに余分なフィールドを増やさないため)。

    python tools/build_data.py            # 全部
    python tools/build_data.py --dry-run  # 差分の有無だけ見る
"""
import collections
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from extract_data import TARGETS, array_span, safe_name  # noqa: E402

BEGIN = ("  // BUILD:%s:start ここから下は tools/build_data.py が %s から"
         "生成しています。直接編集しないこと")
END = "  // BUILD:%s:end"
WRAP = 118


def js_value(v):
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, dict):
        return "{" + ", ".join("%s:%s" % (k, js_value(x)) for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(js_value(x) for x in v) + "]"
    raise TypeError(type(v))


def is_row_list(v):
    """{...} が並ぶ配列か(1行1件で出したいもの)。"""
    return isinstance(v, list) and v and all(isinstance(x, dict) for x in v)


def emit_entry(entry, ind="    "):
    """1件を、今までと同じ書き方のJSリテラルにする。

    **キーの並び順は元のまま保つ。** 並びを変えると JSON.stringify の結果が
    変わってしまい、受け入れ検査(verify_extract.py)が通らなくなる。
    """
    out = []
    for line in entry.get("notes", []):
        out.append("%s// %s" % (ind, line))

    # 連続するスカラーを1かたまり、{...}が並ぶ配列を1かたまりとして区切る
    segs, run = [], []
    for k, v in entry.items():
        if k == "notes":
            continue
        if is_row_list(v):
            if run:
                segs.append(("scalars", run))
                run = []
            segs.append(("block", (k, v)))
        else:
            run.append((k, v))
    if run:
        segs.append(("scalars", run))

    lines = []
    for si, (kind, payload) in enumerate(segs):
        last_seg = si == len(segs) - 1
        if kind == "scalars":
            pieces = ["%s:%s" % (k, js_value(v)) for k, v in payload]
            cur = ""
            for pi, p in enumerate(pieces):
                p += "," if (pi < len(pieces) - 1 or not last_seg) else ""
                if not cur:
                    cur = p
                elif len(cur) + 1 + len(p) + len(ind) + 2 > WRAP:
                    lines.append(cur)
                    cur = p
                else:
                    cur += " " + p
            if cur:
                lines.append(cur)
        else:
            k, rows = payload
            lines.append("%s:[" % k)
            for ri, row in enumerate(rows):
                for line in row.get("note", []):
                    lines.append("  // %s" % line)
                clean = collections.OrderedDict(
                    (kk, vv) for kk, vv in row.items() if kk != "note")
                lines.append("  %s%s" % (js_value(clean),
                                         "," if ri < len(rows) - 1 else ""))
            lines.append("]" + ("" if last_seg else ","))

    lines[-1] += "}"
    out.append(ind + "{" + lines[0])
    out.extend(ind + "  " + x for x in lines[1:])
    return out


def build_array(entries, ind="    "):
    lines = []
    for i, e in enumerate(entries):
        chunk = emit_entry(e, ind)
        if i < len(entries) - 1:
            chunk[-1] += ","
        lines.extend(chunk)
        lines.append("")
    while lines and not lines[-1]:
        lines.pop()
    return lines


def load_entries(outdir, keyfld, order):
    d = os.path.join(ROOT, outdir)
    by_key = {}
    for fn in os.listdir(d):
        if not fn.endswith(".json"):
            continue
        e = json.load(io.open(os.path.join(d, fn), encoding="utf-8"),
                      object_pairs_hook=collections.OrderedDict)
        by_key[safe_name(str(e[keyfld]))] = e
    # 既存の並び順を保つ。新しく増えたものは末尾に、名前順で足す。
    out, used = [], set()
    for k in order:
        if k in by_key:
            out.append(by_key[k])
            used.add(k)
    for k in sorted(set(by_key) - used):
        out.append(by_key[k])
    return out


def current_order(text, array, keyfld):
    lo, hi = array_span(text, array)
    body = text[lo:hi]
    pat = r'\b%s\s*:\s*"([^"]*)"' % keyfld
    seen, out = set(), []
    for m in re.finditer(pat, body):
        k = safe_name(m.group(1))
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def replace_array(text, array, outdir, keyfld):
    """配列の中身を差し替え、生成ブロックのマーカーで囲む。"""
    entries = load_entries(outdir, keyfld, current_order(text, array, keyfld))
    lo, hi = array_span(text, array)
    decl = text.rfind("\n", 0, text.rfind("=", 0, lo)) + 1
    tail = text.find("\n", hi)
    old_head = text[:decl]
    old_tail = text[tail + 1:]
    # すでにマーカーがあれば一緒に消す
    old_head = re.sub(r"[ \t]*// BUILD:%s:start[^\n]*\n" % re.escape(array), "", old_head)
    old_tail = re.sub(r"^[ \t]*// BUILD:%s:end[^\n]*\n" % re.escape(array), "", old_tail)
    mid = ([BEGIN % (array, outdir + "/"),
            "  const %s = [" % array]
           + build_array(entries)
           + ["  ];", END % array])
    return old_head + "\n".join(mid) + "\n" + old_tail, len(entries)


def main(dry=False):
    changed = 0
    for page, array, outdir, keyfld in TARGETS:
        p = os.path.join(ROOT, page)
        if not os.path.exists(p) or not os.path.isdir(os.path.join(ROOT, outdir)):
            continue
        text = io.open(p, encoding="utf-8", newline="").read()
        new, n = replace_array(text, array, outdir, keyfld)
        same = new == text
        print("  %-24s %3d件 %s" % (page, n, "変化なし" if same else "書き換え"))
        if not same and not dry:
            io.open(p, "w", encoding="utf-8", newline="").write(new)
            changed += 1
    print("書き換えたページ %d件%s" % (changed, "(--dry-run)" if dry else ""))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(dry="--dry-run" in sys.argv)
