# -*- coding: utf-8 -*-
"""重い違反のときの巻き戻し。黄丸(reviewedOk:true)を青丸に戻す。

なぜ要るか(C-2、2026-08-12レッドチーム指摘):
ペナルティに「そのセッションで触った該当区分の成果物を巻き戻す」と書いてあったが、
**「そのセッション」も「該当区分」も定義が無かった。** 巻き戻す本人が範囲を決められる以上、
いくらでも狭く取れる。実際2026-08-12は241件を戻したが、それは指示があったからで、
手順として決まっていたわけではない。

ここでは範囲を「起点コミット以降に中身が変わった武将」と機械的に定める。
本人の記憶や自己申告ではなく、gitの履歴から出す。

    python tools/rollback_reviewed.py --since <起点>          # 範囲を出すだけ
    python tools/rollback_reviewed.py --since <起点> --apply  # 実際に青丸へ戻す

起点の決め方(RULE-OPERATION.md「巻き戻しの範囲」):
 ・違反が混入した可能性のある最初のコミット。特定できないなら、
 ・そのセッションの最初のコミットの親。それも分からないなら、
 ・直近で第三者(ユーザー)が確認した時点。
**迷ったら古いほうを起点にする。** 狭く取るほうの誤りは検出できないため。
"""
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
FILES = {"characters.html": "generals", "characters-kyoku.html": "kyokuGenerals",
         "characters-ketsu.html": "ketsuGenerals"}


def git(*a):
    return subprocess.run(["git"] + list(a), cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def entries_at(ref, path, var, tmpdir):
    """指定コミット時点のエントリを {No: そのエントリの中身} で返す。"""
    from audit_characters import extract_array
    o = git("show", "%s:%s" % (ref, path))
    if o.returncode != 0:
        return {}
    p = os.path.join(tmpdir, path.replace("/", "_"))
    io.open(p, "w", encoding="utf-8", newline="").write(o.stdout)
    try:
        return {g.get("no"): g for g in extract_array(p, var)}
    except Exception:
        return {}


def entry_span(s, no):
    """データファイル中の、その武将のエントリの範囲 (開始, 終了)。"""
    for q in ('"', "'"):
        k = s.find("no:%s%s%s" % (q, no, q))
        if k < 0:
            k = s.find("no: %s%s%s" % (q, no, q))
        if k >= 0:
            break
    if k < 0:
        return None
    i = s.rfind("{", 0, k)          # エントリの開き括弧まで戻る
    while i > 0 and s[i:i + 2] not in ("{n", "{ ", "{\n"):
        i = s.rfind("{", 0, i)
    d = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            d += 1
        elif s[j] == "}":
            d -= 1
            if d == 0:
                return (i, j + 1)
    return None


def main():
    if "--since" not in sys.argv:
        print(__doc__)
        return 1
    since = sys.argv[sys.argv.index("--since") + 1]
    apply_ = "--apply" in sys.argv
    if git("rev-parse", "--verify", since).returncode != 0:
        print("起点 %s を解決できない。" % since)
        return 1

    import tempfile
    tmp = tempfile.mkdtemp(prefix="rollback_")
    total = 0
    for path, var in FILES.items():
        full = os.path.join(ROOT, path)
        if not os.path.exists(full):
            continue
        before = entries_at(since, path, var, tmp)
        s = io.open(full, encoding="utf-8", newline="").read()
        from audit_characters import extract_array
        now = {g.get("no"): g for g in extract_array(full, var)}

        # 起点以降に中身が変わった / 新しく増えた武将のうち、黄丸になっているもの
        hits = [no for no, g in now.items()
                if g.get("reviewedOk") and before.get(no) != g]
        if not hits:
            continue
        print("%s: %d件" % (path, len(hits)))
        for no in sorted(hits):
            print("   No.%-6s %s%s" % (no, now[no].get("name", ""),
                                       "  ← 起点に存在しない(新規)" if no not in before else ""))
        total += len(hits)
        if apply_:
            for no in hits:
                sp = entry_span(s, no)
                if not sp:
                    print("   ! No.%s のエントリ位置を特定できない。手で直す。" % no)
                    continue
                a, b = sp
                seg = s[a:b]
                for pat in ("reviewedOk:true", "reviewedOk: true", "reviewedOk:!0"):
                    if pat in seg:
                        seg = seg.replace(pat, pat.replace("true", "false").replace("!0", "false"))
                        break
                s = s[:a] + seg + s[b:]
            io.open(full, "w", encoding="utf-8", newline="").write(s)
            print("   → 青丸に戻した")

    print()
    if total == 0:
        print("起点 %s 以降に変わった黄丸は無い。" % since)
    elif apply_:
        print("合計 %d件を青丸へ戻した。prerender と gen_detail_pages を回してからコミットする。" % total)
    else:
        print("合計 %d件が対象。実際に戻すには --apply を付ける。" % total)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
