# -*- coding: utf-8 -*-
"""ブランチ保護と門番を外すコマンドだけを止める(P-2)。

`.claude/settings.local.json` の allow に `Bash(gh api *)` があるので、
`gh api -X DELETE repos/…/branches/master/protection` が**確認プロンプト無しで**通る。
外す → push → 戻す、で痕跡はリポジトリに一切残らない。
ブランチ保護の有無を見る検査もリポジトリ内に無かった(2026-08-13 第4回レッドチーム)。

ルールの訂正は都度確認なしで進めたい、という運用なので、`gh api` 全体は許可のまま。
**保護そのものを外す/弱める形だけ**を拒否する。

## 2026-08-13 第5回での作り直し

初版は正規表現の列挙だったので、次の2種類の穴が同時にあった。

 * **書き方を1つ変えるだけで抜けた**(10体中8体が指摘)。
   `-XDELETE`(くっつける)、`--method=DELETE`(等号)、`git commit -n`(短縮形)、
   `git -c core.hooksPath=…`(サブコマンドの前に置く)、`GIT_CONFIG_KEY_0=…`(環境変数)、
   `bash -c "…"`(引用符でくるむ)、`.git/hooks/pre-commit` を直接上書き、
   `-X PUT` で保護を骨抜きにする、など。列挙に無い形は素通りだった。
 * **読むだけのコマンドまで止めた**(V-5/X-6/Z-4)。
   `git config --get core.hooksPath` や、この穴を説明する文章を含む
   `git commit -m "…core.hooksPath…"` が止まっていた。

なので、正規表現の当てはめをやめ、**コマンドを token に分解して、
何のコマンドの、どの引数か**を見る。読み取りは通し、書き換えだけ止める。
`bash -c` / `sh -c` / `python -c` のように文字列でくるまれた場合は、中身を再帰的に見る。

    .claude/settings.json の PreToolUse(matcher: Bash|PowerShell)から呼ばれる。
    echo '{"tool_input":{"command":"..."}}' | python tools/hooks/no_protection_bypass.py
    python tools/hooks/no_protection_bypass.py --selftest   # 止めるべき形/通すべき形の確認
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cmdline import base as _base, both as _both      # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
sys.stdin.reconfigure(encoding="utf-8")

# 保護そのものを指す URL / API パス
PROTECT = re.compile(r"(branches/[^\s/]+/protection|/rulesets|"
                     r"required_status_checks|enforce_admins)", re.I)
READ_METHODS = ("GET", "HEAD", "OPTIONS")
# 文字列を受け取って中身を実行するもの(中を再帰的に見る)
WRAPPERS = {"bash": ("-c",), "sh": ("-c",), "zsh": ("-c",), "dash": ("-c",),
            "python": ("-c",), "python3": ("-c",), "py": ("-c",),
            "node": ("-e", "--eval"), "perl": ("-e",),
            "powershell": ("-c", "-command", "-encodedcommand"),
            "pwsh": ("-c", "-command"), "cmd": ("/c", "/k"), "eval": ()}
# ファイルを書き換える側のコマンド(.git/hooks に向いていたら止める)
WRITERS = {"rm", "mv", "cp", "ln", "chmod", "install", "tee", "sed", "truncate",
           "dd", "python", "python3", "py", "node", "perl", "sh", "bash",
           "remove-item", "move-item", "copy-item", "set-content",
           "add-content", "new-item", "out-file", "clear-content", "del",
           "erase", "ren", "attrib"}
GIT_HOOK_DIR = re.compile(r"\.git[\\/]+hooks", re.I)


def _opt_value(toks, i, names):
    """`--method DELETE` / `--method=DELETE` / `-XDELETE` のどれでも値を取る。"""
    t = toks[i]
    for n in names:
        if t == n:
            return toks[i + 1] if i + 1 < len(toks) else ""
        if t.startswith(n + "="):
            return t[len(n) + 1:]
        if len(n) == 2 and n.startswith("-") and t.startswith(n) and len(t) > 2:
            return t[2:]
    return None


def _method(toks, names, body_flags, upload_flags=()):
    """HTTP メソッドを決める。明示が無ければ、本文があるかで GET/POST を決める。"""
    for i in range(len(toks)):
        v = _opt_value(toks, i, names)
        if v:
            return v.strip("'\"").upper()
    for t in toks:
        if t in upload_flags:
            return "PUT"
        if t in body_flags or any(t.startswith(f + "=") for f in body_flags):
            return "POST"
    return "GET"


def _check_git(toks):
    # サブコマンドの前に置ける大域オプション。ここに core.hooksPath を隠せた。
    i, sub, hooks_path = 1, None, None
    while i < len(toks):
        t = toks[i]
        if t in ("-c", "--config-env"):
            v = toks[i + 1] if i + 1 < len(toks) else ""
            if "hookspath" in v.lower():
                hooks_path = v
            i += 2
            continue
        if t.startswith("-c") and len(t) > 2 and "hookspath" in t.lower():
            hooks_path = t
            i += 1
            continue
        if t in ("-C", "--git-dir", "--work-tree", "--exec-path", "--namespace"):
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        sub = t
        break
    if hooks_path:
        return ("フックの置き場所の差し替え(門番を丸ごと外せる)", hooks_path)
    rest = toks[i + 1:] if sub else []
    low = [x.lower() for x in rest]

    if sub == "config":
        # EG-1(第10回、高): core.hooksPath 一語しか見ていなかった。
        # 下のキーは git が**自分で起動する**ので、書ければ任意実行になる。
        RUNNABLE = ("core.fsmonitor", "core.pager", "core.editor",
                    "sequence.editor", "core.sshcommand", "diff.external",
                    "credential.helper", "init.templatedir",
                    "core.gitproxy", "protocol.ext.allow", "uploadpack.",
                    "receivepack.", "alias.", "filter.", "difftool.",
                    "mergetool.", "gpg.program", "ssh.variant")
        for j, t in enumerate(low):
            if any(t.endswith(k) or k in t for k in RUNNABLE):
                nxt = rest[j + 1] if j + 1 < len(rest) else ""
                if nxt and not nxt.startswith("-"):
                    return ("git が自分で起動する設定への書き込み",
                            " ".join(rest[j:j + 2]))
        for j, t in enumerate(low):
            if t.endswith("core.hookspath"):
                if any(o in low for o in ("--unset", "--unset-all",
                                          "--remove-section", "--replace-all")):
                    return ("フックの置き場所の変更", " ".join(rest))
                nxt = rest[j + 1] if j + 1 < len(rest) else ""
                if nxt and not nxt.startswith("-"):   # 値を渡す = 書き込み
                    return ("フックの置き場所の変更", " ".join(rest[j:j + 2]))
        return None                                    # 読むだけは通す

    if sub in ("commit", "merge", "push", "am", "revert", "cherry-pick"):
        if "--no-verify" in low:
            return ("検査(フック)の迂回", "%s --no-verify" % sub)
        # `-n` は commit だけが --no-verify。push は --dry-run、merge は --no-stat。
        if sub == "commit" and ("-n" in low or
                                any(re.fullmatch(r"-[a-mo-z]*n[a-mo-z]*", x)
                                    for x in low)):
            return ("検査(フック)の迂回", "commit -n")
    if sub == "push":
        if any(x in low for x in ("--force", "-f")) or \
                any(x.startswith("--force-with-lease") or
                    x.startswith("--force-if-includes") for x in low):
            return ("保護ブランチへの強制上書き", "push --force")
        # EE-2(第10回、高): refspec の先頭 `+` は --force と同義。
        # 語の列挙だけを見ていたので、`push origin +master` が素通りした。
        for x in rest:
            if x.startswith("+") and not x.startswith("+-"):
                return ("保護ブランチへの強制上書き(+refspec)", "push " + x)
    if sub == "update-index" and any(x in low for x in ("--skip-worktree",
                                                        "--assume-unchanged")):
        return ("検査の道具の変更を git から隠す操作", " ".join(rest))
    if sub in ("update-ref", "filter-branch") and "refs/heads/master" in low:
        return ("master の書き換え", " ".join(rest))
    return None


def _check_gh(toks):
    low = [x.lower() for x in toks]
    if "api" in low:
        k = low.index("api")
        rest = toks[k + 1:]
        method = _method(rest, ("-X", "--method"),
                         ("-f", "--field", "-F", "--raw-field", "--input"))
        path = ""
        skip = False
        for j, t in enumerate(rest):
            if skip:
                skip = False
                continue
            if t.startswith("-"):
                if t in ("-X", "--method", "-f", "--field", "-F", "--raw-field",
                         "-H", "--header", "-q", "--jq", "-t", "--template",
                         "--input", "-p", "--preview", "--cache", "--hostname"):
                    skip = True
                continue
            path = t
            break
        if PROTECT.search(path) and method not in READ_METHODS:
            return ("ブランチ保護の削除・弱体化(%s)" % method, "%s %s" % (method, path))
        if method not in READ_METHODS and any(PROTECT.search(t) for t in rest):
            return ("ブランチ保護の削除・弱体化(%s)" % method, " ".join(rest)[:80])
    if "repo" in low and "edit" in low:
        for t in low:
            if t.startswith("--default-branch") or t.startswith("--visibility"):
                return ("既定ブランチ/公開範囲の変更", t)
    if "ruleset" in low or "rulesets" in low:
        if any(x in low for x in ("delete", "edit")):
            return ("保護ルールセットの変更", " ".join(toks)[:80])
    return None


def _check_curl(toks):
    method = _method(toks[1:], ("-X", "--request"),
                     ("-d", "--data", "--data-raw", "--data-binary", "-F", "--form"),
                     upload_flags=("-T", "--upload-file"))
    url = ""
    for t in toks[1:]:
        if "://" in t or "api.github.com" in t:
            url = t
            break
    if url and PROTECT.search(url) and method not in READ_METHODS:
        return ("ブランチ保護の削除・弱体化(%s)" % method, "%s %s" % (method, url[:60]))
    return None


def _check_segment(toks, depth=0):
    if not toks:
        return None
    # 先頭の環境変数(`GIT_CONFIG_KEY_0=core.hooksPath …`)
    while toks and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]):
        name, _, val = toks[0].partition("=")
        if name.upper().startswith("GIT_CONFIG") and "hookspath" in val.lower():
            return ("環境変数によるフックの置き場所の差し替え", toks[0])
        if name.upper() in ("GIT_DIR", "GIT_WORK_TREE") and depth == 0:
            pass
        toks = toks[1:]
    if not toks:
        return None
    cmd = _base(toks[0])

    # .git/hooks を書き換える(門番の実体を消す・空にする)
    if any(GIT_HOOK_DIR.search(t) for t in toks):
        if cmd in WRITERS or GIT_HOOK_DIR.search(toks[0]):
            return ("門番そのもの(.git/hooks)の書き換え",
                    next(t for t in toks if GIT_HOOK_DIR.search(t)))

    # 文字列でくるまれた中身を見る
    if cmd in WRAPPERS and depth < 3:
        flags = WRAPPERS[cmd]
        for j, t in enumerate(toks):
            if t.lower() in flags and j + 1 < len(toks):
                hit = check(toks[j + 1], depth + 1)
                if hit:
                    return (hit[0] + "(%s -c の中)" % cmd, hit[1])
        if cmd == "eval" and len(toks) > 1:
            hit = check(" ".join(toks[1:]), depth + 1)
            if hit:
                return (hit[0] + "(eval の中)", hit[1])

    if cmd == "git":
        return _check_git(toks)
    if cmd == "gh":
        return _check_gh(toks)
    if cmd in ("curl", "wget"):
        return _check_curl(toks)
    return None


def check(cmd, depth=0):
    """止めるなら (理由, 該当箇所) を返す。通すなら None。"""
    for seg in _both(cmd):
        hit = _check_segment(seg, depth)
        if hit:
            return hit
    return None


# --- 止めるべき形 / 通すべき形。--selftest で全部確かめる -----------------------
# 第5回で「この書き方なら抜けられる」と実際に指摘された形を、そのまま並べてある。
MUST_BLOCK = [
    "gh api -X DELETE repos/o/r/branches/master/protection",
    "gh api -XDELETE repos/o/r/branches/master/protection",
    "gh api --method=DELETE repos/o/r/branches/master/protection",
    "gh api --method DELETE repos/o/r/branches/master/protection",
    "gh api repos/o/r/branches/master/protection -X DELETE",
    "gh api -X PUT repos/o/r/branches/master/protection --input weak.json",
    "gh api -X DELETE repos/o/r/branches/master/protection/enforce_admins",
    "gh api -X PATCH repos/o/r/rulesets/1 -f enforcement=disabled",
    "gh repo edit --default-branch dev",
    "git config core.hooksPath /dev/null",
    "git config --unset core.hooksPath",
    "git -c core.hooksPath=/dev/null commit -m x",
    "git -ccore.hooksPath=/dev/null commit -m x",
    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath "
    "GIT_CONFIG_VALUE_0=/dev/null git commit -m x",
    "git commit --no-verify -m x",
    "git commit -n -m x",
    "git commit -nm x",
    "git merge --no-verify topic",
    "git push --no-verify",
    "git push --force origin master",
    "git push --force-with-lease origin master",
    # 第10回 EE-2(高): refspec の `+` は --force と同義
    "git push origin +master",
    "git push origin +HEAD:refs/heads/master",
    "git -C /tmp/x push origin +master",
    # 第10回 EG-1(高): git が自分で起動する設定
    "git config --global core.fsmonitor touch-pwned",
    "git config --global core.pager evil",
    "git config --global alias.st !touch-pwned",
    "git config --global sequence.editor evil",
    "git update-index --skip-worktree tools/precommit_check.py",
    "rm .git/hooks/pre-commit",
    "rm -f .git/hooks/*",
    "echo '' > .git/hooks/pre-push",
    'cp /dev/null ".git/hooks/pre-commit"',
    "Remove-Item .git\\hooks\\pre-commit",
    'bash -c "gh api -X DELETE repos/o/r/branches/master/protection"',
    "sh -c 'git config core.hooksPath /dev/null'",
    'eval "git commit --no-verify -m x"',
    "curl -X DELETE -H 'Authorization: token x' "
    "https://api.github.com/repos/o/r/branches/master/protection",
    "true; gh api -X DELETE repos/o/r/branches/master/protection",
    "git status && git commit --no-verify -m x",
]
MUST_PASS = [
    "gh api repos/o/r/branches/master/protection",
    "gh api -X GET repos/o/r/branches/master/protection",
    "gh api repos/o/r/branches/master/protection --jq .enforce_admins",
    "git config --get core.hooksPath",
    "git commit -m 'core.hooksPath を変える操作を止めた(P-2)'",
    'git commit -m "gh api -X DELETE …/protection を拒否する"',
    "git push origin work",
    "git push -n origin master",
    "git config --get core.pager",
    "git push origin master",
    "git merge --no-ff topic",
    "git status --porcelain",
    "python tools/install_hooks.py",
    "python tools/precommit_check.py --mode commit",
    "grep -rn 'core.hooksPath' tools/",
    "git log --oneline -5",
]


def selftest():
    ng = 0
    for c in MUST_BLOCK:
        if not check(c):
            print("  NG  止まらない: %s" % c)
            ng += 1
    for c in MUST_PASS:
        hit = check(c)
        if hit:
            print("  NG  止めてはいけないのに止まる: %s\n      → %s" % (c, hit[0]))
            ng += 1
    print("止めるべき %d件 / 通すべき %d件 / 失敗 %d件"
          % (len(MUST_BLOCK), len(MUST_PASS), ng))
    return 1 if ng else 0


def main():
    if "--selftest" in sys.argv[1:]:
        return selftest()
    try:
        cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "")
    except Exception:
        return 0
    hit = check(cmd)
    if not hit:
        return 0
    why, where = hit
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason":
                "P-2: %s にあたるので止めた。該当箇所: 「%s」。"
                "門番やブランチ保護を外す操作は、外したこと自体が"
                "リポジトリに残らないので機械的に拒否している。"
                "本当に必要なときはユーザーに実行してもらうこと"
                "(こちらから迂回路を探さない)。"
                % (why, str(where)[:80])
        }
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
