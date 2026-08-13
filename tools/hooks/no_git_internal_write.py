# -*- coding: utf-8 -*-
"""`.git/` の中身を Write / Edit で直接書き換えるのを止める(P-2の抜け穴)。

T-4 / V-4 / AA-6(2026-08-13 第5回、3体が指摘):

`.claude/settings.json` の PreToolUse は matcher が `Bash|PowerShell` だけだった。
つまり **Write / Edit ツールは一切見張られていない**。
`no_protection_bypass.py` が `rm .git/hooks/pre-commit` を拒否しても、
Write ツールで `.git/hooks/pre-commit` に `exit 0` と書けば同じことができ、
しかもコマンドを一度も実行していないので、どのフックにも引っかからなかった。

`.git/` の中は git が管理する領域で、人(やエージェント)が直接書く理由が無い。
門番の正本は `tools/hooks/` にあり、`install_hooks.py` が複製する。
`.github/` は別物なので通す(`.git` の直後が区切り文字のときだけ止める)。

    echo '{"tool_input":{"file_path":"..."}}' | python tools/hooks/no_git_internal_write.py
    python tools/hooks/no_git_internal_write.py --selftest
"""
import json
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stdin.reconfigure(encoding="utf-8")

# `.git` の直後が区切り文字/終端のときだけ。`.github/...` は当たらない。
GIT_DIR = re.compile(r"(^|[\\/])\.git([\\/]|$)")


def check(path):
    return bool(GIT_DIR.search((path or "").replace("\\", "/")))


MUST_BLOCK = [
    ".git/hooks/pre-commit",
    "c:/Users/uesug/ixa-simulator/.git/hooks/pre-push",
    r"c:\Users\uesug\ixa-simulator\.git\hooks\pre-merge-commit",
    ".git/config",
    "../.git/hooks/pre-commit",
]
MUST_PASS = [
    "tools/hooks/pre-commit",
    ".github/workflows/rules.yml",
    "c:/Users/uesug/ixa-simulator/.github/workflows/rules.yml",
    "docs/RULES.md",
    ".gitignore",
    "tools/precommit_check.py",
]


def selftest():
    ng = 0
    for p in MUST_BLOCK:
        if not check(p):
            print("  NG  止まらない: %s" % p)
            ng += 1
    for p in MUST_PASS:
        if check(p):
            print("  NG  止めてはいけないのに止まる: %s" % p)
            ng += 1
    print("止めるべき %d件 / 通すべき %d件 / 失敗 %d件"
          % (len(MUST_BLOCK), len(MUST_PASS), ng))
    return 1 if ng else 0


def main():
    if "--selftest" in sys.argv[1:]:
        return selftest()
    try:
        ti = json.load(sys.stdin).get("tool_input", {})
    except Exception:
        return 0
    path = ti.get("file_path") or ti.get("notebook_path") or ""
    if not check(path):
        return 0
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason":
                "P-2: `.git/` の中(%s)を直接書き換えようとしている。"
                "門番(.git/hooks/*)はここに実体があるので、書ければ検査を無効化できる。"
                "正本は tools/hooks/ にあり、反映は python tools/install_hooks.py で行う。"
                % str(path)[:80]
        }
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
