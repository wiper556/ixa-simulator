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


def hooks_dir():
    """core.hooksPath を設定されている場合があるので git に聞く。
    worktree では .git がファイルなので、パス解決も git に任せる。"""
    def git(*a):
        r = subprocess.run(["git"] + list(a), cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        return r.stdout.strip() if r.returncode == 0 else ""
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


def diffs():
    """(フック名, 状態) のリスト。空なら全部一致している。"""
    d = hooks_dir()
    if d is None:
        return [("(gitリポジトリではない)", "確認できない")]
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
