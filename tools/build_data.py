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

■一覧ページには「一覧に要る分」しか書かない(2026-08-14)

以前はここで全フィールドを書き出していたので、一覧を開くだけで
鍛錬表・合成表・スキル本文まで全部ダウンロードすることになっていた。
characters.html 533KB / characters-kyoku.html 504KB / skills.html 548KB。
そのうち約8割は、一覧の表示にも検索にも使われない詳細データだった。

詳細は busho/{No}.html ・ skill/{名前}.html に分かれており、
そちらは data/ から直接組み立てる(gen_detail_pages.py)。
つまり一覧ページに詳細を積む理由がもう無い。

    LIST_FIELDS … 一覧に載せるフィールド。ここに無いものは data/ にだけ置く。

**フィールドを一覧で使いたくなったら、まず LIST_FIELDS に足す。**
足さずに使うと undefined になる(ページのJSは静かに空欄を描く)。

■コメント

data/ の "notes"(要素全体) と 行の "note" は**書き戻さない**。
以前はページ側に // として復元していたが、正本が data/ に移った今、
ページは生成物でしかなく、そこに出典の記録を積む意味が無い
(3ページ合計で約1,500行あった)。記録は data/*.json 側にある。

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

# 一覧ページに載せるフィールド(配列名 → フィールド名の集合)。
# 中身は「一覧の表・並べ替え・検索・●マークが実際に読んでいるもの」だけ。
# 詳細だけで使うフィールド(鍛錬表・合成表・成長値・スキル本文など)は載せない。
#
# 武将の一覧が読むもの:
#   表   no / name / ch / cost / troop / sub / effect / imageChar(ホバーの顔出し)
#   検索 furigana / initialSkill
#   ●   imageFull(画像の有無) / approved / reviewedOk
#
# スキルの一覧が読むもの:
#   表   name / rank
#   検索 sourceCharacters[].name(所持武将の名前で引けるようにしてある)
#
# 傑(ketsuGenerals)は一覧に数値まで並べる作りなので、対象が増える。
BUSHO_LIST_FIELDS = [
    "no", "name", "furigana", "ch", "cost", "troop", "sub", "effect",
    "imageChar", "imageFull", "initialSkill", "approved", "reviewedOk",
]
LIST_FIELDS = {
    "generals": BUSHO_LIST_FIELDS,
    "kyokuGenerals": BUSHO_LIST_FIELDS,
    "kyokuPsGenerals": BUSHO_LIST_FIELDS,
    "parallelGenerals": BUSHO_LIST_FIELDS,
    "tokuSecretGenerals": BUSHO_LIST_FIELDS,
    "ketsuGenerals": BUSHO_LIST_FIELDS + [
        "atkBase", "defBase", "tacticsBase", "lv0Troops"],
    "skills": ["name", "rank", "sourceCharacters"],
}


# 極の種別。カードNo.の100の位で決まる(ユーザー、2026-08-14)。
#
#   0〜3  通常極          枠は黒
#   4〜6  プラチナ極      枠は銀(灰色)
#   7〜8  シークレット極  枠は紫  ※将来9まで広がる可能性あり
#
# もともと1000の位の2が極を表していたが、通常極とプラチナが番号の上限まで
# 埋まったので、7千台も極に割り当てられている。だから判定に使うのは
# 1000の位ではなく100の位。
#
# **持たせずに毎回この規則から出す。** No.から一意に決まる値なので、
# JSON側にも書くと片方だけ古くなる余地ができる。9が増えたらここだけ直す。
KYOKU_TYPES = ("通常極", "プラチナ極", "シークレット極")


def kyoku_type(no):
    try:
        h = (int(str(no)) // 100) % 10
    except (TypeError, ValueError):
        return None
    return KYOKU_TYPES[0] if h <= 3 else (KYOKU_TYPES[1] if h <= 6 else KYOKU_TYPES[2])


def derived(entry, array):
    """正本には持たせず、カードNo.から毎回出す値。

    ここが唯一の決め所。ページを組み立てる側(slim)と、
    ページの値が正しいかを見る側(audit_characters.load_source)の
    両方がこれを呼ぶので、規則がずれることがない。
    """
    if array in ("kyokuGenerals", "kyokuPsGenerals"):
        return {"kyokuType": kyoku_type(entry.get("no"))}
    return {}


def slim(entry, array):
    """一覧に載せる分だけを、元の並び順のまま抜き出す。

    並び順を変えないのが要点。JSON.stringify の結果が変わると
    verify_extract.py の突き合わせが通らなくなる。
    """
    fields = LIST_FIELDS.get(array)
    if fields is None:
        return entry
    out = collections.OrderedDict(
        (k, v) for k, v in entry.items() if k in fields)
    out.update(derived(entry, array))
    return out


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

    notes / note(出典の記録)は書き出さない。正本は data/*.json 側にある。
    """
    out = []

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
    entries = [slim(e, array) for e in
               load_entries(outdir, keyfld, current_order(text, array, keyfld))]
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


# ============ シミュレーターの武将並べ替え用の章データ ============
# attack-simulator.html の武将候補は「レアリティ → 章の新しい順 → カードNo.の大きい順」で
# 並べる。章は data/busho*/{No}.json の ch にしかないので、ここから
# assets/js/ixa-data.js の generalChapters に書き出す。
#
# **手で書かないこと。** 章を1件でも更新したら build_data.py を回せば追従する。
# 回し忘れは check_generated.py(pre-push と CI)が止める。
CHAPTERS_FILE = "assets/js/ixa-data.js"
CHAPTERS_BEGIN = ("// BUILD:generalChapters:start ここから下は tools/build_data.py が "
                  "data/busho*/ から生成しています。直接編集しないこと")
CHAPTERS_END = "// BUILD:generalChapters:end"
CHAPTER_DIRS = ("data/busho", "data/busho-kyoku", "data/busho-kyoku-ps", "data/busho-ketsu")


def collect_chapters():
    """カードNo. → 章番号。「未確認」や章の無い武将は入れない。"""
    out = {}
    for d in CHAPTER_DIRS:
        full = os.path.join(ROOT, d)
        if not os.path.isdir(full):
            continue
        for fn in os.listdir(full):
            if not fn.endswith(".json"):
                continue
            with io.open(os.path.join(full, fn), encoding="utf-8") as f:
                e = json.load(f)
            m = re.match(r"^(\d+)章$", str(e.get("ch") or ""))
            if m and e.get("no") is not None:
                out[str(e["no"])] = int(m.group(1))
    return out


def build_chapters_block(chapters):
    lines = [CHAPTERS_BEGIN, "const generalChapters = {"]
    row = "  "
    for no in sorted(chapters, key=lambda k: (len(k), k)):
        piece = '"%s":%d, ' % (no, chapters[no])
        if len(row) + len(piece) > WRAP:
            lines.append(row.rstrip())
            row = "  "
        row += piece
    if row.strip():
        lines.append(row.rstrip().rstrip(","))
    lines.append("};")
    lines.append(CHAPTERS_END)
    return "\n".join(lines)


def replace_chapters(dry=False):
    p = os.path.join(ROOT, CHAPTERS_FILE)
    text = io.open(p, encoding="utf-8", newline="").read()
    lo = text.find(CHAPTERS_BEGIN)
    hi = text.find(CHAPTERS_END)
    if lo < 0 or hi < 0:
        print("  %-24s [停止] BUILD:generalChapters のマーカーが無い" % CHAPTERS_FILE)
        return 1
    chapters = collect_chapters()
    new = text[:lo] + build_chapters_block(chapters) + text[hi + len(CHAPTERS_END):]
    same = new == text
    print("  %-24s %3d件 %s" % (CHAPTERS_FILE, len(chapters),
                                "変化なし" if same else "書き換え"))
    if not same and not dry:
        io.open(p, "w", encoding="utf-8", newline="").write(new)
        return 1
    return 0


# 攻撃シミュレーターに防御スキルの武将を、防御シミュレーターに攻撃スキルの武将を
# 出さないための仕分け。軸は武将DBの troop(「全攻」「槍砲器防」「全攻防」「全攻破」など)
# から取る。「全」「特」「部隊長」「合流」のように軸を持たない書き方もあるので、
# その場合は入れない(入れなければシミュレーター側は両モードに出す)。
AXIS_BEGIN = ("// BUILD:generalSkillAxis:start ここから下は tools/build_data.py が "
              "data/busho*/ の troop から生成しています。直接編集しないこと")
AXIS_END = "// BUILD:generalSkillAxis:end"


def collect_axis():
    """カードNo. → 'atk' / 'def' / 'both'。決まらない武将は入れない。"""
    out = {}
    for d in CHAPTER_DIRS:
        full = os.path.join(ROOT, d)
        if not os.path.isdir(full):
            continue
        for fn in os.listdir(full):
            if not fn.endswith(".json"):
                continue
            with io.open(os.path.join(full, fn), encoding="utf-8") as f:
                e = json.load(f)
            t = str(e.get("troop") or "")
            a, b = "攻" in t, "防" in t
            v = "both" if a and b else ("atk" if a else ("def" if b else None))
            if v and e.get("no") is not None:
                out[str(e["no"])] = v
    return out


def build_axis_block(axis):
    lines = [AXIS_BEGIN, "const generalSkillAxis = {"]
    row = "  "
    for no in sorted(axis, key=lambda k: (len(k), k)):
        piece = '"%s":"%s", ' % (no, axis[no])
        if len(row) + len(piece) > WRAP:
            lines.append(row.rstrip())
            row = "  "
        row += piece
    if row.strip():
        lines.append(row.rstrip().rstrip(","))
    lines.append("};")
    lines.append(AXIS_END)
    return "\n".join(lines)


def replace_axis(dry=False):
    p = os.path.join(ROOT, CHAPTERS_FILE)
    text = io.open(p, encoding="utf-8", newline="").read()
    lo = text.find(AXIS_BEGIN)
    hi = text.find(AXIS_END)
    if lo < 0 or hi < 0:
        print("  %-24s [停止] BUILD:generalSkillAxis のマーカーが無い" % CHAPTERS_FILE)
        return 1
    axis = collect_axis()
    new = text[:lo] + build_axis_block(axis) + text[hi + len(AXIS_END):]
    same = new == text
    print("  %-24s %3d件 %s (攻防の仕分け)"
          % (CHAPTERS_FILE, len(axis), "変化なし" if same else "書き換え"))
    if not same and not dry:
        io.open(p, "w", encoding="utf-8", newline="").write(new)
        return 1
    return 0


def main(dry=False):
    changed = 0
    for page, array, outdir, keyfld in TARGETS:
        p = os.path.join(ROOT, page)
        if not os.path.exists(p) or not os.path.isdir(os.path.join(ROOT, outdir)):
            continue
        text = io.open(p, encoding="utf-8", newline="").read()
        new, n = replace_array(text, array, outdir, keyfld)
        same = new == text
        print("  %-24s %3d件 %s (%dKB→%dKB)"
              % (page, n, "変化なし" if same else "書き換え",
                 len(text.encode("utf-8")) // 1024,
                 len(new.encode("utf-8")) // 1024))
        if not same and not dry:
            io.open(p, "w", encoding="utf-8", newline="").write(new)
            changed += 1
    changed += replace_chapters(dry)
    changed += replace_axis(dry)
    print("書き換えたページ %d件%s" % (changed, "(--dry-run)" if dry else ""))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(dry="--dry-run" in sys.argv)
