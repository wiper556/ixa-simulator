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


def main():
    try:
        cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "")
    except Exception:
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
