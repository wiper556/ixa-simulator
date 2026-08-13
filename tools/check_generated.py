# -*- coding: utf-8 -*-
"""公開されるページが、いまのデータから作れるものと一致しているかを見る。

なぜ要るか(I-9/I-10、2026-08-13 第3回レッドチーム):

 I-9  `busho/10062.html` を手で書き換えて「攻撃力9999」と入れても、
      commit も push も check_js も通った。監査は元データしか見ておらず、
      **公開されるページと監査対象のデータが一致している保証がどこにも無かった。**
      データを直さずページだけ直す/ページだけ直してデータを放置、どちらも通る。

 I-10 再生成漏れの警告(P-01/P-02)は構造的に空振りしていた。
      P-01 は「characters.html に PRERENDER という文字列があるか」を見ていたが、
      その文字列は常駐しているので**入力ファイル自身が出力に数えられていた**。
      P-02 は無関係な生成物1枚をステージするだけで満たせた。

推測でなく、実際に生成して比べる。`git worktree` で追跡ファイルだけを取り出し、
そこで生成器を回して差分を見る(作業ディレクトリには一切触れない)。

**この検査を作る過程で、生成器の重い不具合が1つ見つかった。**
`gen_detail_pages.py` は `ROOT` を `c:\\Users\\uesug\\ixa-simulator` と決め打ちし、
起動直後に `os.chdir(ROOT)` していた。つまり**どこで実行しても本体を再生成する**。
CI(Linux)では起動した瞬間に落ち、worktree や clone で回しても検査にならなかった。
スクリプトの位置から根を求めるように直してある。

**捕まえられる範囲(クローンで実験して確認):**
 ・データを変えたのに再生成していない → 捕まえる
 ・生成物を手で書き換えた → 捕まえる
併せて pre-commit 側にも「データも道具も触らずに生成物だけをコミットしていないか」を置いた。

    python tools/check_generated.py            # HEAD を検査
    python tools/check_generated.py <ref>      # 任意のコミットを検査

所要はおよそ1分。pre-commit では回さず、pre-push と CI で回す。
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# lastmod は生成した日付が入るので、日付だけの差は無視する
VOLATILE = {"sitemap.xml": re.compile(r"<lastmod>[^<]*</lastmod>")}


def run(cmd, cwd=ROOT):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main():
    ref = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "HEAD"
    sha = run(["git", "rev-parse", "--verify", "--quiet", ref + "^{commit}"]).stdout.strip()
    if not sha:
        print("%s をコミットとして解決できない。" % ref)
        return 1

    wt = tempfile.mkdtemp(prefix="gencheck_")
    shutil.rmtree(wt, ignore_errors=True)      # worktree add は空でないと嫌がる
    r = run(["git", "worktree", "add", "--detach", "--quiet", wt, sha])
    if r.returncode != 0:
        print("作業ツリーを作れない: " + (r.stderr or "")[-300:])
        return 1
    try:
        # build_data を先頭に置くのが要点(2026-08-13の作り替え)。正本は
        # data/busho*/ と data/skill/ に移り、ページ内の配列は生成物になった。
        # ここで作り直すことで、次の2つが**どちらも**同じ1つの検査で止まる:
        #   ・data/ を直したのにページを再生成していない
        #   ・ページの配列を手で書き換えた(data/ から作り直すと消える)
        for tool in ("build_data.py", "gen_detail_pages.py", "prerender.py"):
            r = run([sys.executable, os.path.join("tools", tool)], cwd=wt)
            if r.returncode != 0:
                print("[停止] %s が失敗した。" % tool)
                print((r.stdout or "")[-800:])
                print((r.stderr or "")[-800:])
                return 1
            # prerender は JSエラーのページを黙って飛ばすので、その報告も拾う
            for line in (r.stdout or "").split("\n"):
                if "JSエラーのため中止" in line:
                    print("  ! " + line.strip())

        changed = [l for l in run(["git", "status", "--porcelain"], cwd=wt).stdout.split("\n")
                   if l.strip()]
        real = []
        for l in changed:
            path = l[3:].strip().strip('"')
            pat = VOLATILE.get(path)
            if pat is None:
                real.append(path)
                continue
            a = run(["git", "show", "%s:%s" % (sha, path)]).stdout
            b = io.open(os.path.join(wt, path), encoding="utf-8", newline="").read()
            if pat.sub("", a) != pat.sub("", b):
                real.append(path)

        if not real:
            print("公開されるページは、いまのデータから作れるものと一致している。")
            return 0
        print("=" * 62)
        print("[停止] 生成物とデータが食い違っている: %d件" % len(real))
        print("=" * 62)
        for p in real[:20]:
            print("  " + p)
        if len(real) > 20:
            print("  ... ほか%d件" % (len(real) - 20))
        print()
        print("原因は次のどれか:")
        print("  ・data/ を変えたのに再生成していない(P-01/P-02)")
        print("    → python tools/build_data.py → prerender.py → gen_detail_pages.py の順に回す")
        print("  ・生成物を手で書き換えた")
        print("    → busho/ skill/ は生成物なので手で編集しない。data/ 側を直す")
        print("  ・characters*.html / skills.html の中の配列を手で書き換えた")
        print("    → 配列も生成物になった。正本は data/busho*/{No}.json と data/skill/{名前}.json")
        return 1
    finally:
        run(["git", "worktree", "remove", "--force", wt])
        shutil.rmtree(wt, ignore_errors=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
