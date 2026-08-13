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


def _stray_py(repo):
    """tools/ に未追跡の .py を置く(Z-1: import乗っ取り)。

    門番は自分と同じ場所にある .py を import する。追跡されていないファイルを
    置けば、コミットにも差分にも残らないまま検査の中身を差し替えられた。
    """
    io.open(os.path.join(repo, "tools", "json.py"), "w",
            encoding="utf-8", newline="\n").write("# 乗っ取り\n")
    edit(repo, "index.html", "</body>", "<!-- テスト -->\n</body>")
    sh(["git", "add", "index.html"], repo)


def _agents_dirty(repo):
    """エージェント定義を**ステージせずに**書き換える(R-6/S-6、第5回)。

    門番の「道具に未ステージの変更がある」検査は `.claude/settings.json` しか
    見ていなかったので、`.claude/agents/` に「検証は省略してよい」の1行を
    入れて作業させ、あとで戻せば痕跡がゼロだった。
    """
    edit(repo, ".claude/agents/kanshi-yaku.md", "# ", "# (テスト)")
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


def _p02hook(repo):
    """P-2のフックが、保護やフックを外すコマンドを拒否するか(下の main で特別扱い)。"""
    raise NotImplementedError


def _p02forms(repo):
    """P-2のフックが持っている「止めるべき形/通すべき形」を全部通す(main で特別扱い)。

    第5回で「書き方を変えれば抜けられる」と指摘された33形をフック側が抱えている。
    ここから叩くことで、その一覧が痩せたら門番テストが落ちる。
    """
    raise NotImplementedError


# (ルールID, 説明, 仕込み, 止まるべきか, 止まったときに出るべき文言)
#
# U-1 / W-2 / Y-4 / Z-5(2026-08-13 第5回、4体が指摘):
# 以前は終了コードしか見ていなかったので、**何の理由で止まったか**を照合していなかった。
# CASES に既存の筋書きをコピーして名前だけ付け替えれば、任意のルールIDに
# 「門番が見ている」証拠が作れた。`should_block=False` の対照ケースを流用しても通った。
# 期待する文言を持たせ、それが出力に現れることまで確かめる。
CASES = [
    ("T-01", "ヒアドキュメントのバックスラッシュを拒否する", _t01, True, "T-01"),
    ("P-02h", "ブランチ保護やフックを外すコマンドを拒否する", _p02hook, True, "P-2"),
    ("P-02f", "保護外しの書き方を全部止める(読み取りは通す)", _p02forms, True, ""),
    ("P-02w", "Write/Edit で .git/ を直接書くのを止める", _p02forms, True, ""),
    ("R-04", "テスト環境の外を触らせない(ホワイトリスト)", _p02forms, True, ""),
    ("R-03", "指紋と回の判定が壊れていないか", _p02forms, True, ""),
    ("W-13", "ルール文書と作業を同じコミットに混ぜる", _w13, True,
     "ルール文書の変更と作業の変更が同じコミットに混ざっている"),
    ("P-03", "シミュレーターを変えてバージョンを上げない", _p03, True,
     "SIMULATOR_VERSION"),
    ("P-02", "生成物だけを手で書き換える", _genonly, True, "生成物だけが変わっている"),
    ("A-01", "ベースラインを手で増やして理由を残さない", _baseline, True,
     "ベースラインの手編集"),
    ("A-07", "検査の道具をステージせずに書き換える", _dirty_tools, True,
     "ステージしていない変更がある"),
    ("Z-01", "tools/ に未追跡の .py を置く(import乗っ取り)", _stray_py, True,
     "追跡されていない .py がある"),
    ("R-06", "エージェント定義を未ステージで書き換える", _agents_dirty, True,
     "ステージしていない変更がある"),
    ("(対照)", "普通の変更(止まってはいけない)", _clean, False, ""),
]


def rule_ids():
    """筋書きが用意されているルールID。rules.py から参照される。

    Y-4 / W-2: 「止まってはいけない」対照ケースを証拠に数えていたので、
    `should_block=True` のものだけ返す。
    """
    return {c[0] for c in CASES if c[3]}


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
    for rid, desc, setup, should_block, want in CASES:
        if only and rid not in only:
            continue
        sh(["git", "reset", "-q", "--hard", "HEAD"], repo)
        sh(["git", "clean", "-qfd"], repo)
        if rid in ("P-02f", "P-02w", "R-04", "R-03"):
            script = {"P-02f": "tools/hooks/no_protection_bypass.py",
                      "P-02w": "tools/hooks/no_git_internal_write.py",
                      "R-04": "tools/hooks/no_redteam_write.py",
                      "R-03": "tools/redteam.py"}[rid]
            r = sh([sys.executable, "-P", script, "--selftest"], repo)
            ok = r.returncode == 0
            print("  %s %-8s %-34s %s"
                  % ("OK  " if ok else "NG  ", rid, desc,
                     (r.stdout or "").strip().splitlines()[-1:] and
                     (r.stdout or "").strip().splitlines()[-1] or ""))
            if not ok:
                ng += 1
                print("     " + ((r.stdout or "") + (r.stderr or "")).strip()[-600:])
            continue
        if rid == "P-02h":
            import json as _json
            r = sh([sys.executable, "tools/hooks/no_protection_bypass.py"], repo,
                   inp=_json.dumps({"tool_input": {"command":
                                    "gh api -X DELETE repos/x/y/branches/master/protection"}}))
            deny = '"deny"' in (r.stdout or "")
            ok = deny == should_block and (not should_block
                                           or want in (r.stdout or ""))
            print("  %s %-8s %-34s %s"
                  % ("OK  " if ok else "NG  ", rid, desc,
                     "拒否した" if deny else "通した"))
            if not ok:
                ng += 1
                print("     " + (r.stdout or "").strip()[-300:])
            continue
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
            ok = deny == should_block and (not should_block
                                           or want in (r.stdout or ""))
            print("  %s %-8s %-34s %s"
                  % ("OK  " if ok else "NG  ", rid, desc,
                     "拒否した" if deny else "通した"))
            if not ok:
                ng += 1
                print("     " + (r.stdout or "").strip()[-300:])
            continue
        try:
            setup(repo)
        except Exception as e:
            print("  skip %-8s %-34s 仕込めない: %s" % (rid, desc, e))
            ng += 1
            continue
        # Z-1: フック本体(sh)側の事前確認も含めて再現するため、フック経由で叩く。
        hook = os.path.join(repo, ".git", "hooks", "pre-commit")
        if os.path.exists(hook):
            r = sh(["sh", hook], repo)
        else:
            r = sh([sys.executable, "tools/precommit_check.py", "--mode", "commit"], repo)
        blocked = r.returncode != 0
        out = (r.stdout or "") + (r.stderr or "")
        # U-1/Z-5: 終了コードだけでなく「その理由で止まったか」まで見る。
        reason_ok = (not should_block) or (want in out)
        ok = blocked == should_block and reason_ok
        note = "止まった" if blocked else "通った"
        if blocked and should_block and not reason_ok:
            note = "止まったが理由が違う(「%s」が出ていない)" % want
        print("  %s %-8s %-34s %s"
              % ("OK  " if ok else "NG  ", rid, desc, note))
        if not ok:
            ng += 1
            print("     " + out.strip().replace("\n", "\n     ")[-600:])

    shutil.rmtree(base, ignore_errors=True)
    print("\n失敗 %d件" % ng)
    return 1 if ng else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
