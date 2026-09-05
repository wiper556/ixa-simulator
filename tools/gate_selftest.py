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
    python tools/gate_selftest.py --shard=1/3   # 3分割の1本目だけ(CIの並列用)

`--shard=i/n` は CASES の並び順で i 番目の組だけを走らせる。**ケースIDを外に
書き写さない**ので、CASES に足したものが自動でどれかの組に入る。分割の一覧を
ワークフロー側に持つと、ケースを足したときに片側だけ更新されて
「足したのにどこでも走らない」が起きる(S-14と同じ形)。

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


def _w13_other_docs(repo):
    """docs/ の、RULE で始まらない文書を作業と混ぜる。

    2026-08-14: フックは `docs/RULE…` だけをルール文書とみなしていたので、
    docs/synthesis-gaps-2026-08-14.md(作業メモ)は素通りし、CIだけが止めた。
    上の _w13 は docs/RULE-OPERATION.md を使うので、この抜けを踏まなかった。
    """
    edit(repo, "docs/data-audit-2026-08-12.md", "\n", "\nテスト\n")
    edit(repo, "index.html", "</body>", "<!-- テスト -->\n</body>")
    sh(["git", "add", "docs/data-audit-2026-08-12.md", "index.html"], repo)


def _w13_lock_ok(repo):
    """錠前(tools/checks.lock)と作業を同じコミットに入れる。**止まってはいけない。**

    2026-08-14(ユーザー承認): 錠前は生成物で、道具と母集団が同時に動く変更では
    どの順に分けても一時的に食い違う。W-13の対象から外した。
    外したことが後から静かに戻らないよう、通ることを筋書きとして固定する。
    """
    # 中身の意味は変えない(空行を1つ入れるだけ)。錠前の値を動かすと
    # 「監査チェックが消えた」など別の停止条件に当たって、W-13を試せなくなる。
    edit(repo, "tools/checks.lock", '{\n "cause_tags"', '{\n\n "cause_tags"')
    edit(repo, "index.html", "</body>", "<!-- テスト -->\n</body>")
    sh(["git", "add", "tools/checks.lock", "index.html"], repo)


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
    # 2026-08-16: ベースラインが空(`[]`)のときに `[,{…}]` という壊れたJSONを
    # 作っていて、門番が JSONDecodeError で落ち「止まったが理由が違う」になった。
    # 監査が0件ならベースラインは空になるので、空も正しい状態として扱う。
    sep = "" if s.rstrip().endswith("[") else ","
    s += '%s\n {"cat": "テスト", "sev": "HIGH", "msg": "手で足した"}\n]' % sep
    io.open(p, "w", encoding="utf-8", newline="\n").write(s)
    sh(["git", "add", "tools/audit_baseline.json"], repo)


def _d14_pick(repo):
    """赤丸の筋書きに使うカードを、その時点の中身から選ぶ。

    2026-08-19: ここは No.1310 を直に書いていた。1310 が 2026-08-16 に
    記録つきの正式な赤丸になったため、「記録を残さずに新しく赤丸にする」
    状況をもう作れず、D-14 と D-14b が黙って空振りしていた
    (門番は正しく動いていたが、動く証拠のほうが消えていた)。
    番号を覚えさせると同じ古び方をするので、条件で選ぶ。

    条件: まだ赤丸でない / 許可記録に無い / 差し込む目印がページに1箇所だけ

    2026-08-19(同日2度目): 最初は「黄丸である」も条件にしていたが、重い違反の
    巻き戻しで黄丸が0件になった瞬間に候補が尽きて筋書きが動かなくなった。
    reviewedOk の行は false でも残るので、値ではなく行の存在だけを見る。
    """
    rec = set()
    p = os.path.join(repo, "tools", "approvals.txt")
    if os.path.exists(p):
        for line in io.open(p, encoding="utf-8", newline=""):
            rec.add(line.split("\t")[0].strip())
    html = io.open(os.path.join(repo, "characters.html"),
                   encoding="utf-8", newline="").read()
    for m in re.finditer(r'no:"(\d{3,6})", furigana:"', html):
        no = m.group(1)
        if no in rec or html.count('no:"%s", furigana:"' % no) != 1:
            continue
        j = os.path.join(repo, "data", "busho", no + ".json")
        if not os.path.exists(j):
            continue
        s = io.open(j, encoding="utf-8").read()
        if '"approved"' in s or ' "reviewedOk":' not in s:
            continue
        return no
    raise RuntimeError("D-14 の筋書きに使えるカードが無い"
                       "(赤丸でない黄丸が1件も見つからない)")


def _d14_marks(repo):
    """選んだカードを赤丸にする。正本とページの両方を同じ内容にそろえる。

    片方だけ直すと「ページの配列が正本と違う」で止まってしまい、
    赤丸の検査が働いたのか別の検査で止まったのか見分けが付かない。
    """
    no = _d14_pick(repo)
    edit(repo, "data/busho/%s.json" % no, ' "reviewedOk":',
         ' "approved": true,\n "reviewedOk":')
    edit(repo, "characters.html", 'no:"%s", furigana:"' % no,
         'no:"%s", approved:true, furigana:"' % no)
    sh(["git", "add", "data/busho/%s.json" % no, "characters.html"], repo)
    return no


def _d14(repo):
    """許可の記録を残さずに赤丸にする(止まるべき)。"""
    _d14_marks(repo)


def _d14ok(repo):
    """許可の記録つきで赤丸にする(止まってはいけない)。

    記録は --approve に書かせる。ここで手書きすると、
    「手編集を止める検査」のほうに引っかかって別の理由で赤くなる。
    """
    no = _d14_marks(repo)
    sh([sys.executable, "tools/precommit_check.py", "--approve", no,
        "--reason", "自己テスト用。ユーザーが赤丸にしてよいと明言した想定"], repo)
    sh(["git", "add", "tools/approvals.txt"], repo)


def _d14edit(repo):
    """許可の記録を手で書く(止まるべき)。過去行の書き換えも同じ扱い。"""
    no = _d14_marks(repo)
    p = os.path.join(repo, "tools", "approvals.txt")
    with io.open(p, "a", encoding="utf-8", newline="\n") as f:
        f.write("%s なんとなく\n" % no)       # タブ区切りでも日付でもない
    sh(["git", "add", "tools/approvals.txt"], repo)


def _s14_rate(repo):
    """くじの排出確率の表を手で書き換える(止まるべき)。

    2026-08-19(S-14の3件目): くじを差し替えたとき、抽選に使う定数だけ直して
    ページ下部の表を旧いくじのまま残した。表示と実際の抽選が7箇所ずれていた。
    表は build_data.py が定数から作るので、手で書き換えれば
    「生成物とデータが食い違っている」で止まる。
    """
    s = io.open(os.path.join(repo, "gacha-simulator.html"),
                encoding="utf-8", newline="").read()
    m = re.search(r'(<td data-label="単発 / 10連1〜9枚目">)([\d.]+)(%</td>)', s)
    if not m:
        raise RuntimeError("排出確率の表が見つからない")
    old = m.group(0)
    new = m.group(1) + ("9.999" if m.group(2) != "9.999" else "8.888") + m.group(3)
    edit(repo, "gacha-simulator.html", old, new)
    sh(["git", "add", "gacha-simulator.html"], repo)


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
    ("T-08", "コミットメッセージのコマンド置換を拒否する", _t01, True, "T-08"),
    ("P-02h", "ブランチ保護やフックを外すコマンドを拒否する", _p02hook, True, "P-2"),
    ("P-02f", "保護外しの書き方を全部止める(読み取りは通す)", _p02forms, True, ""),
    ("P-02w", "Write/Edit で .git/ を直接書くのを止める", _p02forms, True, ""),
    ("R-04", "テスト環境の外を触らせない(ホワイトリスト)", _p02forms, True, ""),
    ("R-03", "指紋と回の判定が壊れていないか", _p02forms, True, ""),
    ("W-13", "ルール文書と作業を同じコミットに混ぜる", _w13, True,
     "ルール文書の変更と作業の変更が同じコミットに混ざっている"),
    ("W-13b", "docs/ の他の文書を作業と混ぜる(CIとの食い違い)", _w13_other_docs, True,
     "ルール文書の変更と作業の変更が同じコミットに混ざっている"),
    ("W-13c", "錠前と作業を混ぜる(止まってはいけない)", _w13_lock_ok, False, ""),
    ("P-03", "シミュレーターを変えてバージョンを上げない", _p03, True,
     "SIMULATOR_VERSION"),
    ("P-02", "生成物だけを手で書き換える", _genonly, True, "生成物だけが変わっている"),
    ("A-01", "ベースラインを手で増やして理由を残さない", _baseline, True,
     "ベースラインの手編集"),
    ("D-14", "記録を残さずに赤丸にする", _d14, True,
     "新しく approved:true になった武将"),
    ("D-14b", "許可の記録つきで赤丸にする(止まってはいけない)", _d14ok, False, ""),
    ("D-14c", "許可の記録を手で書く", _d14edit, True,
     "赤丸の許可記録の手編集"),
    ("S-14", "くじの排出確率の表を手で書き換える", _s14_rate, True,
     "生成物とデータが食い違っている", "push"),
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


def case_weight(case):
    """ケースの重さの目安。分割の釣り合いを取るのに使う。

    2026-09-05: 位置で機械的に振り分けたら、**21件中ただ1つ `push` 経路を使う
    S-14 のケースが1つの組に入り、その組だけ396秒・他は約95秒**になった。
    push 経路は門番が check_generated(全ページの再生成)を丸ごと回すので、
    commit 経路の約20倍かかる(実測 約300秒 対 約13秒)。重さを見て配る。
    """
    route = case[5] if len(case) > 5 else "commit"
    return 20.0 if route == "push" else 1.0


def shard_members(cases, shard):
    """重い順に、そのとき最も空いている組へ配る(LPT)。

    **ケースIDをワークフローに書き写さないための仕組み。** 並びや件数が変わっても
    全ケースがどれか1つの組に必ず入る(欠けも重複も出ない)。
    """
    i, n = shard
    load = [0.0] * n
    mine = set()
    for pos in sorted(range(len(cases)), key=lambda x: (-case_weight(cases[x]), x)):
        k = min(range(n), key=lambda x: (load[x], x))
        load[k] += case_weight(cases[pos])
        if k == i - 1:
            mine.add(pos)
    return mine


def parse_shard(argv):
    """`--shard=i/n` を読む。無ければ None。"""
    for a in argv:
        if a.startswith("--shard="):
            i, n = a[len("--shard="):].split("/")
            i, n = int(i), int(n)
            if not (1 <= i <= n):
                raise SystemExit("--shard=i/n の i は 1..n")
            return (i, n)
    return None


def main():
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    shard = parse_shard(sys.argv[1:])
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
    ran = 0
    mine = shard_members(CASES, shard) if shard else None
    if shard:
        print("分割 %d/%d を走らせる(%d件)" % (shard[0], shard[1], len(mine)))
    for pos, case in enumerate(CASES):
        rid, desc, setup, should_block, want = case[:5]
        # 6番目があれば門番の経路。既定は commit。
        # 生成物の照合(check_generated)は重いので push だけで走る作りになっている。
        # 門番の位置をテストの都合で動かさず、実際に効いている経路を叩く。
        route = case[5] if len(case) > 5 else "commit"
        if only and rid not in only:
            continue
        # 重さを見て振り分ける。CASES に足したものは必ずどれかの組に入る。
        if mine is not None and pos not in mine:
            continue
        ran += 1
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
        if rid in ("T-01", "T-08"):
            # PreToolUse フックは git の外側なので、スクリプトを直接叩いて判定を見る
            import json as _json
            bs = chr(92)
            if rid == "T-08":
                # 二重引用符の中のバックティックはシェルが実行してしまう。
                # 同じ事故を2回起こして、どちらも push 後に気づいた。
                cmd = ('git commit -m "本文に ' + chr(96) + 'git for-each-ref'
                       + chr(96) + ' を書く"')
            else:
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
        if route == "push":
            # push の門番は「これから外に出る中身」を見るので、先に1つ積む。
            # CI には git の名前・メールが無いので、環境に依存しないよう -c で渡す
            # (2026-08-19: 手元では自分の設定が効いて通り、CIだけ skip になった)。
            c = sh(["git", "-c", "user.name=gate_selftest",
                    "-c", "user.email=gate_selftest@example.invalid",
                    "commit", "-q", "--no-verify", "-m", "自己テスト"], repo)
            if c.returncode != 0:
                print("  skip %-8s %-34s コミットできない: %s"
                      % (rid, desc, ((c.stderr or c.stdout or "").strip()[-120:])))
                ng += 1
                continue
            hook = os.path.join(repo, ".git", "hooks", "pre-push")
            if os.path.exists(hook):
                r = sh(["sh", hook], repo)
            else:
                r = sh([sys.executable, "tools/precommit_check.py",
                        "--mode", "push"], repo)
        else:
            hook = os.path.join(repo, ".git", "hooks", "pre-commit")
            if os.path.exists(hook):
                r = sh(["sh", hook], repo)
            else:
                r = sh([sys.executable, "tools/precommit_check.py",
                        "--mode", "commit"], repo)
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
    print("\n%d件を走らせて 失敗 %d件" % (ran, ng))
    # 分割の指定が悪くて1件も走らなかったら、緑にせず気づかせる。
    if shard and ran == 0:
        print("[停止] 分割 %d/%d に当たるケースが1件も無い" % shard)
        return 1
    return 1 if ng else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
