# -*- coding: utf-8 -*-
"""門番(precommit_check.py)だけが持っている停止条件が、本当に止めるかを確かめる。

なぜ要るか(L-1 / K-3 / M-4、2026-08-13 第4回レッドチーム):

 L-1 違反ログの「監査に足したか」に `足した(pre-commit)` と書けば、
     いちばん重い「2回目=作業停止」が解除できた。
     **pre-commit がそのルールを本当に見ているかは誰も検査していなかった。**
 K-3 `precommit_check.py` はハッシュ対象外で、CIも一度も実行しない。
     そこにしか無い停止条件(W-13/P-03/生成物の手編集ほか)は痕跡なく消せた。
 M-4 同上。フックを迂回すればCIでは再検査されない。

`audit_selftest.py` が「監査のチェックが鳴るか」を見るのと同じことを、門番についてやる。
使い捨てのクローンで、わざと引っかかる状況を作って、止まるかどうかを見る。

    python tools/gate_selftest.py           # 全部
    python tools/gate_selftest.py W-13      # ルールIDで絞る

`rules.py` は違反ログの「足した(pre-commit)」を、ここにそのルールIDの筋書きが
あるときだけ認める。
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sh(args, cwd, inp=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", input=inp)


def edit(repo, rel, old, new):
    p = os.path.join(repo, rel)
    s = io.open(p, encoding="utf-8", newline="").read()
    if old not in s:
        raise RuntimeError("%s に「%s」が無い" % (rel, old[:40]))
    io.open(p, "w", encoding="utf-8", newline="").write(s.replace(old, new, 1))


# --- 筋書き。(ルールID, 説明, 仕込み, 止まるべきか) ---------------------------

def _w13(repo):
    """ルール文書の変更と作業の変更を同じコミットに混ぜる。"""
    edit(repo, "docs/RULE-OPERATION.md", "## 設計方針", "## 設計方針(テスト)")
    edit(repo, "index.html", "</body>", "<!-- テスト -->\n</body>")
    sh(["git", "add", "docs/RULE-OPERATION.md", "index.html"], repo)


def _p03(repo):
    """attack-simulator.html を変えたのに SIMULATOR_VERSION の値を上げない。"""
    edit(repo, "attack-simulator.html", "</body>", "<!-- テスト -->\n</body>")
    sh(["git", "add", "attack-simulator.html"], repo)


def _genonly(repo):
    """生成物(busho/)だけを手で書き換える。"""
    edit(repo, "busho/10062.html", "</body>", "<p>テスト</p>\n</body>")
    sh(["git", "add", "busho/10062.html"], repo)


def _baseline(repo):
    """ベースラインを手で増やす(理由を残さない)。"""
    p = os.path.join(repo, "tools", "audit_baseline.json")
    s = io.open(p, encoding="utf-8").read().rstrip()
    assert s.endswith("]"), "ベースラインの形が想定と違う"
    s = s[:-1].rstrip().rstrip(",")
    s += ',\n {"cat": "テスト", "sev": "HIGH", "msg": "手で足した"}\n]'
    io.open(p, "w", encoding="utf-8", newline="\n").write(s)
    sh(["git", "add", "tools/audit_baseline.json"], repo)


def _dirty_tools(repo):
    """検査に使う道具を、ステージせずに書き換える。"""
    edit(repo, "tools/audit_characters.py", "# -*- coding: utf-8 -*-",
         "# -*- coding: utf-8 -*-\n# テスト")
    edit(repo, "index.html", "</body>", "<!-- テスト -->\n</body>")
    sh(["git", "add", "index.html"], repo)


def _clean(repo):
    """何も悪いことをしない(止まってはいけない)。"""
    edit(repo, "index.html", "</body>", "<!-- テスト -->\n</body>")
    sh(["git", "add", "index.html"], repo)


def _t01(repo):
    """T-01のPreToolUseフックが、危険なヒアドキュメントを拒否するか。

    これだけ門番(precommit)でなく PreToolUse フックの筋書きなので、
    スクリプトを直接叩いて deny が返るかを見る(下の main で特別扱いする)。
    """
    raise NotImplementedError


CASES = [
    ("T-01", "ヒアドキュメントのバックスラッシュを拒否する", _t01, True),
    ("W-13", "ルール文書と作業を同じコミットに混ぜる", _w13, True),
    ("P-03", "シミュレーターを変えてバージョンを上げない", _p03, True),
    ("P-02", "生成物だけを手で書き換える", _genonly, True),
    ("A-01", "ベースラインを手で増やして理由を残さない", _baseline, True),
    ("A-07", "検査の道具をステージせずに書き換える", _dirty_tools, True),
    ("(対照)", "普通の変更(止まってはいけない)", _clean, False),
]


def rule_ids():
    """筋書きが用意されているルールID。rules.py から参照される。"""
    return {c[0] for c in CASES}


def main():
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    base = tempfile.mkdtemp(prefix="gate_")
    repo = os.path.join(base, "repo")
    print("クローンを作る...")
    r = sh(["git", "clone", "--quiet", ROOT.replace("\\", "/"), repo], base)
    if r.returncode:
        print((r.stderr or "")[-400:])
        return 1
    # 作業ツリーの未コミット分も持ち込む(いま直している最中のものを試したいので)
    for rel in ("tools", "docs", ".github", ".claude"):
        s, d = os.path.join(ROOT, rel), os.path.join(repo, rel)
        if os.path.isdir(s):
            shutil.rmtree(d, ignore_errors=True)
            shutil.copytree(s, d, ignore=shutil.ignore_patterns(
                "__pycache__", "audit_out", "worktrees", "sounds",
                "settings.local.json"))
    sh(["git", "add", "-A"], repo)
    sh(["git", "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "検証用"], repo)
    sh([sys.executable, "tools/install_hooks.py"], repo)

    ng = 0
    for rid, desc, setup, should_block in CASES:
        if only and rid not in only:
            continue
        sh(["git", "reset", "-q", "--hard", "HEAD"], repo)
        sh(["git", "clean", "-qfd"], repo)
        if rid == "T-01":
            # PreToolUse フックは git の外側なので、スクリプトを直接叩いて判定を見る
            import json as _json
            bs = chr(92)
            cmd = ("python - <<'PY'" + chr(10)
                   + 's=re.sub(r"(a)", r"' + bs + '1b", s)' + chr(10) + "PY")
            r = sh([sys.executable, "tools/hooks/no_heredoc_backslash.py"], repo,
                   inp=_json.dumps({"tool_input": {"command": cmd}}))
            deny = ('"permissionDecision": "deny"' in (r.stdout or "")
                    or '"permissionDecision":"deny"' in (r.stdout or ""))
            ok = deny == should_block
            print("  %s %-8s %-34s %s"
                  % ("OK  " if ok else "NG  ", rid, desc,
                     "拒否した" if deny else "通した"))
            if not ok:
                ng += 1
            continue
        try:
            setup(repo)
        except Exception as e:
            print("  skip %-8s %-34s 仕込めない: %s" % (rid, desc, e))
            ng += 1
            continue
        r = sh([sys.executable, "tools/precommit_check.py", "--mode", "commit"], repo)
        blocked = r.returncode != 0
        ok = blocked == should_block
        print("  %s %-8s %-34s %s"
              % ("OK  " if ok else "NG  ", rid, desc,
                 "止まった" if blocked else "通った"))
        if not ok:
            ng += 1
            print("     " + (r.stdout or "").strip().replace("\n", "\n     ")[-500:])

    shutil.rmtree(base, ignore_errors=True)
    print("\n失敗 %d件" % ng)
    return 1 if ng else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
