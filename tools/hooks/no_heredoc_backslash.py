# -*- coding: utf-8 -*-
"""Bashのヒアドキュメントにバックスラッシュが入っていたら止める(RULES.md T-01)。

なぜ要るか:
`<<'PY'` のように引用符つきの区切りでも、この環境ではバックスラッシュが1段消費される
ことがある。`\\1` が制御文字 `\\x01` になって置換が「削除」に化けたり、`"\\r\\n"` が
本物の改行になってPythonの構文エラーになったりする。**壊れ方が静かなのが厄介で、
置換が成功したように見えてデータが消える。**

T-01 としてルール化してあるが、2026-08-12 の1セッション中に3回踏んだ。
記憶では守れないと確定したので、機械で止める
(RULE-OPERATION.md「同じルールを2回破ったとき」)。

エスケープを含むスクリプトは Write / Edit でファイルに書いてから実行する。

    .claude/settings.local.json の PreToolUse(matcher: Bash)から呼ばれる。
    echo '{"tool_input":{"command":"..."}}' | python tools/hooks/no_heredoc_backslash.py
"""
import json
import re
import sys


sys.stdout.reconfigure(encoding="utf-8")   # 既定だとcp932になり、日本語の理由文が壊れる
sys.stdin.reconfigure(encoding="utf-8")


def heredoc_bodies(cmd):
    """ヒアドキュメントの本文だけを取り出す。

    E-19(2026-08-12 第2回レッドチーム指摘): 以前の正規表現は区切り語を
    `[A-Za-z_][A-Za-z0-9_]*` に限り、引用も `'` `"` だけを見ていた。
    そのため POSIX で正当な `<<\\TAG`(バックスラッシュで引用)と
    `<<'P-Y'`(ハイフン入り)が素通りした。同じ危険形の別表記なので拾う。
    """
    out = []
    # <<  [-]  [\]  ['|"]  TAG  ['|"]
    for m in re.finditer(r"<<-?\s*(\\?)(['\"]?)([^\s;&|<>()'\"]+)\2", cmd):
        tag = m.group(3)
        rest = cmd[m.end():]
        end = re.search(r"^\s*%s\s*$" % re.escape(tag), rest, re.M)
        out.append((tag, rest[:end.start()] if end else rest))
    return out


def commit_msg_substitution(cmd):
    """`git commit -m "…"` の本文に、シェルが展開してしまう書き方が入っていないか。

    T-08(2026-08-13)。同じ事故を2回起こしている。二重引用符の中では
    バックティックと $( ) はコマンド置換として**実行される**ので、
    コミットメッセージにコードを引用しようとすると、その部分が消えたり
    コマンドの出力に化けたりする。

      1回目: メッセージ中の `s/a/b/` が実行され、本文が "x" になった
      2回目: `C:/…` が消え、`git for-each-ref` が実行されて
             その出力(refs/heads/master の行)がメッセージに埋まった

    どちらも push してから気づいた。P-2 が --force を止めるので直せない。
    書いてしまう前に止める。長い本文は -F <ファイル> で渡す。
    """
    for m in re.finditer(r"""(?:^|[\s;&|(])git\s+(?:-C\s+\S+\s+)?commit\b""", cmd):
        rest = cmd[m.end():]
        for q in re.finditer(r'-m\s*"((?:[^"\\]|\\.)*)"', rest):
            body = q.group(1)
            if "`" in body or "$(" in body:
                return body
    return None


def main():
    try:
        cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "")
    except Exception:
        return 0
    body = commit_msg_substitution(cmd)
    if body:
        bad = [x for x in re.findall(r"`[^`]*`|\$\([^)]*\)", body)][:3]
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason":
                    "T-08: git commit -m \"…\" の本文にコマンド置換が入っている。"
                    "二重引用符の中のバックティックと $( ) はシェルが**実行する**ので、"
                    "その部分が消えたり出力に化けたりする"
                    "(実際に2回、push後に気づいて直せなかった)。"
                    "本文はファイルに書いて git commit -F <ファイル> で渡す。"
                    "該当: %s" % " / ".join(bad)
            }
        }, ensure_ascii=False))
        return 0
    hits = [(t, b) for t, b in heredoc_bodies(cmd) if "\\" in b]
    if not hits:
        return 0
    lines = [l for _t, b in hits for l in b.split("\n") if "\\" in l]
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason":
                "T-01: ヒアドキュメント(<<%s)の本文にバックスラッシュが入っている。"
                "この環境では1段消費されることがあり、置換が静かに壊れる"
                "(過去に \\\\1 が制御文字になってデータが消えた)。"
                "エスケープを含むスクリプトは Write でファイルに書いてから "
                "python <file> で実行する。該当行: %s"
                % (hits[0][0], " / ".join(x.strip()[:80] for x in lines[:3]))
        }
    }, ensure_ascii=False))
    return 0


sys.exit(main())
