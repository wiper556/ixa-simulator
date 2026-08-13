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
import re
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
    """データファイル中の、その武将のエントリの範囲 (開始, 終了)。

    E-11(2026-08-12 第2回レッドチーム指摘): 以前は開き括弧を後方探索するとき
    `("{n", "{ ", "{\\n")` に当たるまで戻っていた。先頭キーが `name`/`no` 以外
    (`{db:"kyoku", ...`)だと当たらず、**1つ前の武将のエントリ**を指した。
    `--apply` すると別人の黄丸を落として対象は残る。
    また文字列の中の `}` で深さ計算が壊れた。

    直し方: 深さを数えながら前へ戻り、深さ0の位置にある `{` を開きとみなす。
    文字列とコメントは飛ばす。範囲内に目的の `no:` が入っていることを確かめる。
    """
    m = re.search(r"""no:\s*["'](%s)["']""" % re.escape(str(no)), s)
    if not m:
        return None
    k = m.start()

    # k から前へ、深さ0で見つかる '{' がエントリの開き
    depth = 0
    i = k
    while i > 0:
        i -= 1
        c = s[i]
        if c == "}":
            depth += 1
        elif c == "{":
            if depth == 0:
                break
            depth -= 1
    else:
        return None

    # そこから前向きに、文字列を飛ばしつつ対応する '}' を探す
    d = 0
    j = i
    n = len(s)
    while j < n:
        c = s[j]
        if c in "\"'":
            q = c
            j += 1
            while j < n and s[j] != q:
                j += 2 if s[j] == "\\" else 1
        elif c == "{":
            d += 1
        elif c == "}":
            d -= 1
            if d == 0:
                span = (i, j + 1)
                # 取れた範囲に目的のエントリが入っているか(別人を掴んでいないか)
                return span if i <= k < j else None
        j += 1
    return None


FAILED = 0
FLOOR = "docs/rollback-floor.txt"


def floor_ref():
    """これより新しい起点は選べない、という下限。**HEADの中身から読む。**

    Q-4 / P-3(2026-08-13 第4回レッドチーム): 以前は作業ツリーのファイルを読んでいた。
    しかも `gate_source_clean()` の監視対象に `docs/` が入っていなかったので、
    **未ステージで書き換え → 実行 → 元に戻す** で痕跡ゼロで下限を動かせた。
    さらに単独コミットにすればW-13にも引っかからなかった。
    コミットされた内容から読むことで、少なくとも履歴に残る形でしか動かせなくする。

    E-10(2026-08-12 第2回レッドチーム指摘): 起点を指定するのは巻き戻す本人なので、
    `--since HEAD` と書けば対象0件にできた(実証済み)。「迷ったら古いほうを取る」は
    文書にあるだけで、狭い起点を拒む仕組みが無かった。

    `docs/rollback-floor.txt` にユーザーが最後に確認した時点のコミットを書いておき、
    それより新しい起点を拒否する。この下限を動かせるのはユーザーの確認があったときだけ。
    """
    r = git("show", "HEAD:" + FLOOR)
    text = r.stdout if r.returncode == 0 else ""
    if not text:
        return None
    for line in text.split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            return line.split()[0]
    return None


def main():
    if "--since" not in sys.argv:
        print(__doc__)
        return 1
    since = sys.argv[sys.argv.index("--since") + 1]
    apply_ = "--apply" in sys.argv
    # F-11(第3回): `--since HEAD^{tree}` はコミットでないため
    # merge-base --is-ancestor が必ず失敗し、下限の検査を素通りできた。
    # コミットとして解決できるものだけを受け付ける。
    r = git("rev-parse", "--verify", "--quiet", since + "^{commit}")
    if r.returncode != 0 or not r.stdout.strip():
        print("起点 %s をコミットとして解決できない。" % since)
        print("(ツリーやタグの中身ではなく、コミットを指定する)")
        return 1
    since = r.stdout.strip()

    fl = floor_ref()
    # G-2/H-8/I-4(第3回、3体が指摘): 下限が無い/解決できないときに
    # 「注意」を出してそのまま続行していた。ファイルを消すだけで下限が消え、
    # 消したこと自体も誰も見ていなかった。無ければ止める(フェイルクローズ)。
    if not fl:
        print("[停止] %s が無い。巻き戻しの下限を確認できない。" % FLOOR)
        print("下限が無い状態で巻き戻すと、範囲を自分で好きに狭められる。")
        print("ファイルを復元する: git checkout -- %s" % FLOOR)
        return 1
    if git("rev-parse", "--verify", "--quiet", fl + "^{commit}").returncode != 0:
        print("[停止] 下限 %s をコミットとして解決できない。%s を確認する。" % (fl[:12], FLOOR))
        return 1
    if True:
        # 下限が起点の祖先でない = 起点のほうが古いか無関係 → OK
        # 下限が起点の子孫でもない、という状態も無関係なので許す
        newer = git("merge-base", "--is-ancestor", fl, since).returncode == 0
        same = (git("rev-parse", since).stdout.strip()
                == git("rev-parse", fl).stdout.strip())
        if newer and not same:
            print("[停止] 起点 %s は下限 %s より新しい。" % (since, fl[:12]))
            print()
            print("下限は %s に書いてある「ユーザーが最後に確認した時点」。" % FLOOR)
            print("重い違反の巻き戻しでは、狭く取った誤りは検出できないので、")
            print("下限より古い起点を使う。下限自体を動かすにはユーザーの確認が要る。")
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
            done, failed = 0, []
            for no in hits:
                sp = entry_span(s, no)
                if not sp:
                    failed.append(no)
                    continue
                a, b = sp
                seg = s[a:b]
                hit = False
                for pat in ("reviewedOk:true", "reviewedOk: true", "reviewedOk:!0"):
                    if pat in seg:
                        seg = seg.replace(pat, "reviewedOk:false")
                        hit = True
                        break
                if not hit:
                    # E-11: 置換が空振りしても「戻した」と表示していた。空振りは失敗。
                    failed.append(no)
                    continue
                s = s[:a] + seg + s[b:]
                done += 1
            io.open(full, "w", encoding="utf-8", newline="").write(s)
            print("   → %d件を青丸に戻した" % done)
            if failed:
                print("   ! %d件は書き換えられなかった: %s" % (len(failed), ", ".join(failed)))
                print("     手で直す。戻し漏れたまま先に進まない。")
                global FAILED
                FAILED += len(failed)

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
