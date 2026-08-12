# -*- coding: utf-8 -*-
"""gitフックの導入と、導入済みフックが正本と一致しているかの確認。

なぜ要るか(A-7、2026-08-12レッドチーム指摘):
フック本体は `.git/hooks/` にあり、ここはgit管理外だった。つまり
 ・フックを消しても、書き換えて中身を空にしても、履歴にも差分にも残らない
 ・別のクローンや worktree には最初からフックが無い
という状態で、「機械で止めている」という前提そのものが検証不能だった。

正本を `tools/hooks/` に置き、`.git/hooks/` はその複製とする。
監査(audit_characters.py)と precommit_check.py が毎回この一致を見る。

    python tools/install_hooks.py           # 導入・更新する
    python tools/install_hooks.py --check   # 一致しているかだけ見る(差異があれば終了コード1)
"""
import io
import os
import stat
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "tools", "hooks")
HOOKS = ("pre-commit", "pre-merge-commit", "pre-push")


def git(*a):
    r = subprocess.run(["git"] + list(a), cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else ""


def hooks_dir():
    """core.hooksPath を設定されている場合があるので git に聞く。
    worktree では .git がファイルなので、パス解決も git に任せる。"""
    p = git("config", "--get", "core.hooksPath")
    if p:
        return p if os.path.isabs(p) else os.path.join(ROOT, p)
    # worktree でも共有の .git/hooks を使う(--git-common-dir)
    c = git("rev-parse", "--git-common-dir") or git("rev-parse", "--git-dir")
    if not c:
        return None
    if not os.path.isabs(c):
        c = os.path.join(ROOT, c)
    return os.path.join(c, "hooks")


def default_hooks_dir():
    c = git("rev-parse", "--git-common-dir") or git("rev-parse", "--git-dir")
    if not c:
        return None
    if not os.path.isabs(c):
        c = os.path.join(ROOT, c)
    return os.path.join(c, "hooks")


def diffs():
    """(フック名, 状態) のリスト。空なら全部一致している。"""
    d = hooks_dir()
    if d is None:
        return [("(gitリポジトリではない)", "確認できない")]

    # E-3/E-4(2026-08-12 第2回レッドチーム指摘):
    #  E-3 `core.hooksPath` を空ディレクトリへ向けるとフック3つが全部死ぬ。
    #      検出役を呼ぶのがフック自身なので、無効化すると検出も止まる自己言及構造だった。
    #  E-4 `core.hooksPath` を正本ディレクトリ(tools/hooks)自身へ向けると、
    #      diffs() が同じファイル同士を比べることになり、中身が `exit 0` でも
    #      「一致している」と表示された。運用文書が「貼れ」と言っている出力そのものが偽装できた。
    # 設定されていること自体を異常として報告する。既定の場所以外は認めない。
    dn = default_hooks_dir()
    if os.path.normcase(os.path.abspath(d)) == os.path.normcase(os.path.abspath(SRC)):
        return [("core.hooksPath", "正本ディレクトリ自身(tools/hooks)を指している。"
                                   "自分と自分を比べることになり、整合検査が意味を失う")]
    if dn and os.path.normcase(os.path.abspath(d)) != os.path.normcase(os.path.abspath(dn)):
        return [("core.hooksPath", "既定(%s)ではなく %s を指している。"
                                   "`git config --unset core.hooksPath` で戻す" % (dn, d))]

    out = []
    for h in HOOKS:
        want = io.open(os.path.join(SRC, h), encoding="utf-8", newline="").read()
        dst = os.path.join(d, h)
        if not os.path.exists(dst):
            out.append((h, "導入されていない"))
            continue
        got = io.open(dst, encoding="utf-8", newline="").read()
        # 改行だけの差は許す(チェックアウト設定で変わるため)
        if got.replace("\r\n", "\n") != want.replace("\r\n", "\n"):
            out.append((h, "中身が正本と違う"))
    return out


def install():
    d = hooks_dir()
    if d is None:
        print("gitリポジトリではないので何もしない。")
        return 1
    # 既定以外を指した状態で入れると、入れた先が使われるとは限らない/自分自身に上書きする。
    # 「入れ直せ」の案内どおり実行したのに直らない、を防ぐため先に止める。
    dn = default_hooks_dir()
    same = os.path.normcase(os.path.abspath(d))
    if same == os.path.normcase(os.path.abspath(SRC)) or (
            dn and same != os.path.normcase(os.path.abspath(dn))):
        print("core.hooksPath が %s を指している。" % d)
        print("先に `git config --unset core.hooksPath` で既定へ戻してから入れ直す。")
        return 1
    if not os.path.isdir(d):
        os.makedirs(d)
    for h in HOOKS:
        s = io.open(os.path.join(SRC, h), encoding="utf-8", newline="").read()
        dst = os.path.join(d, h)
        # 改行は LF で書く。CRLF だと sh が "\r" を引数の一部として読む環境がある。
        io.open(dst, "w", encoding="utf-8", newline="\n").write(s)
        os.chmod(dst, os.stat(dst).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print("  導入: %s" % dst)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--check" in sys.argv:
        bad = diffs()
        if not bad:
            print("フック %d件、正本と一致している。" % len(HOOKS))
            sys.exit(0)
        print("[停止] フックが正本と食い違っている:")
        for h, why in bad:
            print("  %-18s %s" % (h, why))
        print()
        print("  python tools/install_hooks.py   で入れ直す。")
        print("  意図して変えたなら tools/hooks/ 側(正本)を直してコミットする。")
        sys.exit(1)
    sys.exit(install())
