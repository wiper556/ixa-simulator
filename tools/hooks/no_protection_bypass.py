# -*- coding: utf-8 -*-
"""ブランチ保護と門番を外すコマンドだけを止める(P-2、2026-08-13 第4回レッドチーム)。

`.claude/settings.local.json` の allow に `Bash(gh api *)` があるので、
`gh api -X DELETE repos/…/branches/master/protection` が**確認プロンプト無しで**通る。
外す → push → 戻す、で痕跡はリポジトリに一切残らない。
ブランチ保護の有無を見る検査もリポジトリ内に無かった。

ルールの訂正は都度確認なしで進めたい、という運用なので、`gh api` 全体は許可のまま。
**保護そのものを外す/弱める形だけ**を拒否する。

    .claude/settings.json の PreToolUse(matcher: Bash|PowerShell)から呼ばれる。
    echo '{"tool_input":{"command":"..."}}' | python tools/hooks/no_protection_bypass.py
"""
import json
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stdin.reconfigure(encoding="utf-8")

# (見つけたら止める形, 説明)
RULES = [
    (re.compile(r"gh\s+api[^\n|;&]*(-X|--method)\s+DELETE[^\n|;&]*branches/[^\s]*/protection",
                re.I),
     "ブランチ保護の削除"),
    (re.compile(r"gh\s+api[^\n|;&]*branches/[^\s]*/protection[^\n|;&]*(-X|--method)\s+DELETE",
                re.I),
     "ブランチ保護の削除"),
    (re.compile(r"gh\s+api[^\n|;&]*(-X|--method)\s+DELETE[^\n|;&]*"
                r"(required_status_checks|enforce_admins)", re.I),
     "必須チェック/管理者適用の解除"),
    (re.compile(r"gh\s+repo\s+edit[^\n|;&]*--(default-branch|visibility)", re.I),
     "既定ブランチ/公開範囲の変更"),
    (re.compile(r"git\s+config[^\n|;&]*core\.hooksPath", re.I),
     "フックの置き場所の変更(門番を丸ごと外せる)"),
    (re.compile(r"git\s+push[^\n|;&]*--no-verify", re.I),
     "push検査の迂回"),
    (re.compile(r"git\s+(commit|merge)[^\n|;&]*--no-verify", re.I),
     "コミット時の検査の迂回"),
]


def main():
    try:
        cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "")
    except Exception:
        return 0
    for pat, why in RULES:
        m = pat.search(cmd or "")
        if not m:
            continue
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
                    % (why, m.group(0)[:80])
            }
        }, ensure_ascii=False))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
