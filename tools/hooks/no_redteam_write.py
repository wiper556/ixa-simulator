# -*- coding: utf-8 -*-
"""レッドチームの回が開いている間、テスト環境の外を触らせない(ホワイトリスト方式)。

## 方式(2026-08-13、ユーザー指示)

**記載がない(許可されていない)ものはすべて不可。**
禁止したい形を数え上げる方式は、第4回・第5回で「書き方を1つ変えるだけ」で
何度も抜けられた(`-XDELETE`、`--method=DELETE`、`bash -c "…"`、`.git/hooks` 直書き…)。
数え上げる側が必ず負ける勝負なので、回が開いている間は逆にする。
**許した形だけを通す。**

許すのは次だけ:

 1. テスト環境(サンドボックス)の中で完結するもの
    - `git -C <サンドボックス>/… …`(サンドボックス内のリポジトリ操作は何でも)
    - `python <サンドボックス>/…`、`sh <サンドボックス>/…`
    - `mkdir` / `cp` / `mv` / `rm` など、**触る先が全部サンドボックスの中**のもの
 2. テスト環境を作るもの
    - `git clone <本物> <サンドボックス>/…`
 3. 本物のリポジトリを**読むだけ**のもの
    - `git log` / `show` / `diff` / `status` / `ls-files` / `rev-parse` / `cat-file` /
      `blame` / `grep` / `describe` / `branch`(一覧) / `remote -v` / `config --get`
    - `cat` / `head` / `tail` / `grep` / `rg` / `ls` / `find` / `wc` / `sort` / `uniq` /
      `diff` / `stat` / `file` / `basename` / `dirname` / `echo` / `true` / `pwd` / `date`
 4. 回そのものを扱うもの
    - `python <本物>/tools/redteam.py …`

これ以外は理由を問わず拒否。書き込み先(リダイレクト)がサンドボックスの外にあるものも拒否。
Write / Edit / NotebookEdit は、**サンドボックスの中のファイル以外すべて拒否**。

回が開いていないときは何もしない(通常の作業を邪魔しない)。

    echo '{"tool_name":"Bash","tool_input":{"command":"..."}}' | python tools/hooks/no_redteam_write.py
    python tools/hooks/no_redteam_write.py --selftest
"""
import hashlib
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cmdline import base, both, strip_env      # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
sys.stdin.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 本物を読むだけの git サブコマンド
GIT_READ = {"log", "show", "diff", "status", "ls-files", "ls-tree", "rev-parse",
            "cat-file", "blame", "grep", "describe", "shortlog", "remote",
            "branch", "tag", "reflog", "whatchanged", "count-objects",
            "rev-list", "for-each-ref", "check-ignore", "var", "version"}
# どこで走っても書き込まない道具
READ_ONLY = {"cat", "head", "tail", "grep", "rg", "ls", "dir", "find", "wc",
             "sort", "uniq", "diff", "stat", "file", "basename", "dirname",
             "echo", "true", "false", "pwd", "date", "which", "type", "nl",
             "cut", "tr", "comm", "md5sum", "sha256sum", "printf", "seq",
             "get-content", "select-string", "get-childitem", "measure-object",
             # 移動するだけで何も書かない。続くコマンドは別区画として個別に見る。
             "cd", "set-location", "pushd", "popd"}


def _norm(p):
    p = (p or "").strip().strip('"').strip("'").replace("\\", "/")
    if re.match(r"^[a-zA-Z]:/", p):
        p = p[0].lower() + p[1:]
    return p.rstrip("/")


def _inside(path, root):
    # 実地1回目の不具合: 引用符を外す前に isabs を見ていたので、
    # `"C:/…/scratchpad/x.py"` のような**引用符つきの絶対パス**が相対パス扱いになり、
    # テスト環境の中なのに外と判定していた(自分がロックアウトされた)。
    p = _norm(path)
    if not p:
        return False
    if not os.path.isabs(p):
        p = _norm(os.path.abspath(p))
    r = _norm(root)
    return bool(r) and (p == r or p.startswith(r + "/"))


def state_dir():
    key = hashlib.sha256(ROOT.lower().encode("utf-8")).hexdigest()[:12]
    return os.path.join(os.path.expanduser("~"), ".claude", "redteam", key)


def active():
    p = os.path.join(state_dir(), "active.json")
    if not os.path.exists(p):
        return None
    try:
        return json.load(io.open(p, encoding="utf-8"))
    except Exception:
        # 読めない = 壊れている。安全側(回が開いている扱い)に倒す。
        return {"label": "(記録が壊れている)", "sandbox": "", "root": ROOT}


def _pathish(tok):
    """パスらしい token か。オプションや値は除く。"""
    if not tok or tok.startswith("-"):
        return False
    return ("/" in tok or "\\" in tok or tok in (".", "..")
            or re.search(r"\.[A-Za-z0-9]{1,6}$", tok) is not None)


def check_segment(toks, sandbox, repo):
    """許した形なら None。拒否するなら理由。"""
    _env, toks = strip_env(toks)
    if not toks:
        return None
    # `> ファイル` のリダイレクト先は、区切りで割ると「パス1個だけの区画」になる。
    if len(toks) == 1 and _pathish(toks[0]):
        if _inside(toks[0], sandbox):
            return None
        return "書き込み先(リダイレクト)がテスト環境の外"
    cmd = base(toks[0])
    args = toks[1:]

    # 4. 回そのものを扱う道具
    # 実地1回目の不具合: `args[0]` だけを見ていたので `python -P tools/redteam.py`
    # (フラグが前に付く形)が通らず、回を閉じる手段が無くなった。
    if cmd in ("python", "python3", "py"):
        script = next((t for t in args if not t.startswith("-")), None)
        if script and base(script) == "redteam.py" and _inside(script, repo):
            return None

    # 1. サンドボックスの中で完結するもの
    if cmd == "git":
        # `git -C <サンドボックス>` なら中の操作は何でもよい
        for i, t in enumerate(args):
            if t == "-C" and i + 1 < len(args):
                if _inside(args[i + 1], sandbox):
                    return None
                break
            if t.startswith("-C") and len(t) > 2:
                if _inside(t[2:], sandbox):
                    return None
                break
        sub = next((t for t in args if not t.startswith("-")), "")
        # 2. テスト環境を作る
        if sub == "clone":
            dst = [t for t in args[args.index(sub) + 1:] if not t.startswith("-")]
            if len(dst) >= 2 and _inside(dst[-1], sandbox):
                return None
            return "clone の作成先がテスト環境の外"
        # 3. 本物を読むだけ
        if sub in GIT_READ:
            if sub == "config" or "--unset" in args:
                return "git config は読み書きの区別が曖昧なので回の間は不可"
            return None
        return "回の間に許した git は、-C でテスト環境を指すか、読み取り専用のものだけ(%s)" % (sub or "?")

    if cmd in ("python", "python3", "py", "sh", "bash", "node", "powershell", "pwsh"):
        target = next((t for t in args if _pathish(t)), None)
        if target and _inside(target, sandbox):
            return None
        return "回の間に走らせてよいスクリプトはテスト環境の中のものだけ"

    if cmd in ("mkdir", "cp", "mv", "rm", "rmdir", "touch", "tee", "chmod",
               "ln", "robocopy", "xcopy", "new-item", "copy-item", "move-item",
               "remove-item", "set-content", "out-file", "add-content"):
        paths = [t for t in args if _pathish(t)]
        if paths and all(_inside(t, sandbox) for t in paths):
            return None
        return "書き込み先がテスト環境の外"

    # 3. 読むだけの道具はどこでもよい
    if cmd in READ_ONLY:
        return None

    return "回の間はホワイトリストに無いコマンドは通さない(%s)" % cmd


def check_command(cmd, sandbox, repo):
    for seg in both(cmd):
        why = check_segment(list(seg), sandbox, repo)
        if why:
            return why, " ".join(seg)[:80]
    # リダイレクトの書き込み先。`>` の右は別 segment の先頭に来るので、
    # 元の文字列からも直接見る(`> ../x` のような相対も拾う)。
    for m in re.finditer(r"(?<![0-9])>{1,2}\s*([^\s;|&]+)", cmd or ""):
        t = m.group(1).strip('"').strip("'")
        if not _inside(t, sandbox):
            return "書き込み先(リダイレクト)がテスト環境の外", t[:80]
    return None, None


def deny(reason, where, a):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason":
                "R-04: レッドチームの回「%s」が開いている間は、テスト環境の中だけで"
                "作業する(ホワイトリスト方式。記載が無いものはすべて不可)。"
                "%s。該当箇所: 「%s」。"
                "テスト環境: %s。"
                "回を閉じるには python tools/redteam.py --end。"
                % (a.get("label", "?"), reason, where, a.get("sandbox", "?"))
        }
    }, ensure_ascii=False))
    return 0


def main():
    if "--selftest" in sys.argv[1:]:
        return selftest()
    try:
        d = json.load(sys.stdin)
    except Exception:
        return 0
    a = active()
    if not a:
        return 0                      # 回が開いていないときは何もしない
    sandbox = a.get("sandbox") or ""
    repo = a.get("root") or ROOT
    ti = d.get("tool_input") or {}
    tool = d.get("tool_name") or ""

    path = ti.get("file_path") or ti.get("notebook_path")
    if path:
        if sandbox and _inside(path, sandbox):
            return 0
        return deny("書き込み先がテスト環境の外", str(path)[:80], a)

    cmd = ti.get("command")
    if cmd:
        why, where = check_command(cmd, sandbox, repo)
        if why:
            return deny(why, where, a)
        return 0

    # 素性の分からないツールは、回の間は通さない(ホワイトリスト方式)
    if tool in ("Write", "Edit", "NotebookEdit", "Bash", "PowerShell"):
        return deny("入力を読み取れないツール呼び出し", tool, a)
    return 0


# --- 通す形 / 止める形 -------------------------------------------------------
SB = "c:/temp/claude"
RP = "c:/repo"
MUST_PASS = [
    "git clone c:/repo c:/temp/claude/ba",
    "git -C c:/temp/claude/ba commit -m x",
    "git -C c:/temp/claude/ba config core.hooksPath /dev/null",
    "python c:/temp/claude/ba/tools/install_hooks.py",
    "python c:/temp/claude/ba/tools/precommit_check.py --mode commit",
    "git log --oneline -8",
    "git show HEAD:tools/lock.py",
    "git status --porcelain",
    "grep -rn 'hooksPath' tools/",
    "cat docs/RULES.md",
    "python c:/repo/tools/redteam.py --check",
    "mkdir c:/temp/claude/ba/x",
    "rm -rf c:/temp/claude/ba",
    "echo hello",
    "git -C c:/temp/claude/ba log > c:/temp/claude/ba/out.txt",
    # 実地1回目で自分がロックアウトされた形。全部通らないといけない。
    'python "c:/temp/claude/ba/tools/check_js.py"',
    "python -P c:/repo/tools/redteam.py --end",
    "cd c:/repo",
    "cd c:/repo && git log --oneline -3",
]
MUST_BLOCK = [
    "python tools/install_hooks.py",              # 本物にフックを入れ直す
    "python c:/repo/tools/precommit_check.py --mode commit",
    "git commit -m x",                            # 本物でコミット
    "git add -A",
    "git checkout -- .",
    "git config core.hooksPath /dev/null",
    "rm c:/repo/.git/hooks/pre-commit",
    "cp c:/temp/claude/ba/tools/lock.py c:/repo/tools/lock.py",
    "echo x > c:/repo/tools/lock.py",
    "echo x > ../ixa-simulator/tools/lock.py",
    "bash -c 'git commit -m x'",
    "git clone c:/repo c:/other/place",
    "sed -i 's/a/b/' tools/rules.py",
    "curl -X DELETE https://api.github.com/repos/o/r/branches/master/protection",
    "npm install",
    "git -C c:/repo commit -m x",
]


def selftest():
    ng = 0
    for c in MUST_PASS:
        why, where = check_command(c, SB, RP)
        if why:
            print("  NG  通すべきなのに止まる: %s\n      → %s (%s)" % (c, why, where))
            ng += 1
    for c in MUST_BLOCK:
        why, _w = check_command(c, SB, RP)
        if not why:
            print("  NG  止まらない: %s" % c)
            ng += 1
    print("通すべき %d件 / 止めるべき %d件 / 失敗 %d件"
          % (len(MUST_PASS), len(MUST_BLOCK), ng))
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
