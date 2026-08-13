# -*- coding: utf-8 -*-
"""監査チェックの自己テスト。「0件」が本当に健全なのか、検査が動いていないだけなのかを分ける。

なぜ要るか(docs/RULE-OPERATION.md「チェックを足すときの義務」):
2026-08-12の違反S-01は、自分で書いた検査がルールより狭いまま自分で合格判定したのが原因だった。
検査が0件を返しても、それが「不備が無い」なのか「検査が動いていない」なのかは区別できない。
そこで**わざと違反を作って、そのチェックが拾うかどうか**を確かめる。

やること: 対象ファイルを退避 → 違反を1つ注入 → 監査を走らせる → 該当種別が出るか確認 → 復元。

    python tools/audit_selftest.py
"""
import collections
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FINDINGS = os.path.join(ROOT, "tools", "audit_out", "findings.json")

VIOL = "docs/RULE-VIOLATIONS.md"


def _viol_rows():
    """違反ログの表のデータ行。行の中身でなく**位置**で指すために使う。"""
    txt = io.open(os.path.join(ROOT, VIOL), encoding="utf-8", newline="").read()
    return [l for l in txt.replace("\r\n", "\n").split("\n")
            if re.match(r"^\|\s*\d{4}-\d{2}-\d{2}\s*\|", l.strip())]


def v_row(i):
    """i行目そのもの。行を消す/増やす系のケースで使う。"""
    rows = _viol_rows()
    return rows[i] if -len(rows) <= i < len(rows) else "@@行が無い@@"


def v_set(i, col, val):
    """i行目のcol列目を val に差し替えた (置換前, 置換後) を返す。

    I-14(2026-08-13 第3回レッドチーム指摘): ここは
    `| 2026-08-12 | W-13 |` や `| 足した(pre-commit) |` のような
    **運用中に普通に書き換わる文字列**をアンカーにしていた。
    違反ログを1行足した日・監査欄を埋めた日に自己テストが赤くなる。
    赤が常態化すると見なくなり、S-01(自作の検査を自分で合格判定)の温床に戻る。
    日付やIDでなく、表の何行目の何列目か、で指す。
    """
    old = v_row(i)
    if old.startswith("@@"):
        return old, old
    cells = old.strip().strip("|").split("|")
    if not (0 <= col < len(cells)):
        return "@@列が無い@@", "@@列が無い@@"
    cells[col] = " %s " % val
    return old, "|" + "|".join(cells) + "|"


# (チェック種別, 触るファイル, 置換前, 置換後)
CASES = [
    # E-14(2026-08-12 第2回レッドチーム指摘): 1ルールに1ケースだと、
    # そのルールの**別の分岐**を丸ごと消しても緑のままになる。
    # 実際、監査から「合成候補の走査」を削除(=違反S-01そのものの再現)しても
    # 19/19 OK・exit 0 で通った。分岐ごとに1ケース置く。
    ("S以上でページ無し", "characters.html",
     # 分岐1: 初期スキル(Aランクは規約上ページ不要なので対象外になる。Sで試す)
     'initialSkill:"天弦ノ威軍"', 'initialSkill:"存在しない架空スキルS"'),
    ("S以上でページ無し", "characters.html",
     # 分岐2: 合成候補。S-01はこちらを数えていなかったのが原因だった。
     'skill:"天弦ノ威軍"', 'skill:"存在しない架空の合成候補SS"'),
    ("sourceCharactersのdb", "skills.html",
     '{name:"佐渡島方治", no:"2614", slot:"S1", db:"kyoku"}',
     '{name:"佐渡島方治", no:"2614", slot:"S1"}'),
    ("trTableの段飛び", "characters-kyoku.html",
     '{level:"TR1", points:"10", effect:null},\n        {level:"TR2", points:"40", effect:null},\n'
     '        {level:"TR3", points:"90", effect:null},\n        {level:"TR4", points:"150", effect:null},\n'
     '        {level:"TR5", points:"200", effect:null},\n        {level:"TR6", points:"パラレル", effect:null}\n'
     '      ],\n      // 合成テーブルはixanaryスキルページ「百識ノ計」',
     '{level:"TR5", points:"200", effect:"テスト"}\n      ],\n'
     '      // 合成テーブルはixanaryスキルページ「百識ノ計」'),
    ("シミュのcost未設定", "assets/js/ixa-data.js",
     "no:'2614', cost: 3,", "no:'2614',"),
    ("effectShortの接頭辞", "characters-kyoku.html",
     'effectShort:"攻撃390%上昇+防御390%上昇+部隊内卓越追加確率+25%',
     'effectShort:"100% / 効果 攻撃390%上昇+防御390%上昇+部隊内卓越追加確率+25%'),
    ("ドット付きランク", "characters-kyoku.html",
     "rankGrades:{yari:'B', yumi:'S', uma:'B', ki:'S'}",
     "rankGrades:{yari:'.B', yumi:'S', uma:'B', ki:'S'}"),
    ("slotの独自語", "skills.html",
     '{name:"佐渡島方治", no:"2614", slot:"S1", db:"kyoku"}',
     '{name:"佐渡島方治", no:"2614", slot:"候補", db:"kyoku"}'),
    ("武将名の表記ゆれ", "characters-kyoku.html",
     '{name:"佐渡島方治", no:"2614"', '{name:"佐渡島方治(2)", no:"2614"'),
    ("データ内のHTMLタグ", "characters-kyoku.html",
     'effect:"部隊内武将の全スキルの卓越追加確率+25%',
     'effect:"<span style=\\"color:red\\">部隊内</span>武将の全スキルの卓越追加確率+25%'),
    ("模倣不可の位置", "characters-kyoku.html",
     # 消すと「模倣不可が無い」扱いで対象外になるので、①より後ろへ移す
     'skillDetail:"A/LV10 確率 100% 攻撃390%上昇/対象:弓・器・焙\\n模倣不可\\n①攻撃390%上昇する',
     'skillDetail:"A/LV10 確率 100% 攻撃390%上昇/対象:弓・器・焙\\n①攻撃390%上昇する\\n模倣不可'),
    ("サイト上の出典言及", "privacy.html",
     "</main>", "<p>出典元: テスト</p>\n  </main>"),
    ("横スクロール対策の欠落", "assets/css/site.css",
     ".site-main{max-width:960px;width:100%;align-self:center;padding:32px 16px 60px;min-width:0;",
     ".site-main{max-width:960px;width:100%;align-self:center;padding:32px 16px 60px;"),
    # --- 2026-08-12に足した、ルール索引・違反ログ・フック自身を見るチェック ---
    # ここを自己テスト無しで置くと、S-01(自作の検査を自分で合格判定)の再演になる。
    ("フックが正本と違う", "tools/hooks/pre-push",
     "--mode push", "--mode push  # 正本を書き換えた"),
    # 索引に行を1つ足すと実数が増え、他文書の「N件の索引」という表記が古くなる。
    # 数字そのものを書き換える形にすると、ルールが増えるたびにこのケースが壊れる。
    ("ルール件数の表記ずれ", "docs/RULES.md",
     "| T-07 |", "| Z-01 | 自己テスト用の架空ルール | - | × | ✗ |\n| T-07 |"),
    # 棚卸しをした日にアンカーが外れるので、日付を含まない前置きで指す。
    # 前に1行差し込むと last_inventory() は先に見つけたほうを読む。
    ("棚卸しの期限切れ", "docs/RULES.md",
     "\n最終棚卸し: ", "\n最終棚卸し: 2020-01-01\n古い記録: "),
    # 以下は違反ログを触る。位置(何行目の何列目)で指すので、
    # 行が増えても監査欄が埋まってもアンカーが壊れない(I-14)。
    ("違反ログのIDが索引に無い", VIOL) + v_set(0, 1, "Z-99"),
    # 監査欄を「まだ」に戻すと、未対応として数えられ、
    # 同時にその根本原因が2回目のまま未解決になる。
    ("違反ログに未対応が残っている", VIOL) + v_set(-1, 7, "まだ"),
    ("2回目の違反で作業停止中", VIOL) + v_set(0, 7, "まだ"),

    ("未確認の根拠なし", "characters-kyoku.html",
     '{level:"TR5", points:"200", effect:null},\n        {level:"TR6", points:"パラレル", effect:null}\n'
     '      ],\n      // 合成テーブルはixanaryスキルページ「百識ノ計」',
     '{level:"TR5", points:"200", effect:"テスト値"},\n        {level:"TR6", points:"パラレル", effect:null}\n'
     '      ],\n      // 合成テーブルはixanaryスキルページ「百識ノ計」'),
]


# 「検査を黙らせる書き方」。違反そのものより見つけにくいので、別枠で試す。
# (種別, 触るファイル, 置換前, 置換後, 何を試しているか)
EVASIONS = [
    # 違反ログを触るケースは、日付やIDでなく「何行目の何列目」で指す(I-14)。
    # 行が増えても監査欄が埋まってもアンカーが壊れない。
    ("違反ログの行を解釈できない", VIOL) + v_set(0, 0, "2026/08/12")
    + ("日付の区切りを / に変える",),
    ("違反ログの行を解釈できない", VIOL,
     v_row(2), "|".join(v_row(2).strip().strip("|").split("|")[:-1]).join(["|", "|"]),
     "列を減らして行を短くする"),
    # 行頭の空白は正規化して読むので「解釈できない」にはならない。
    # 正しい期待は「その行が数え落とされないこと」なので、行数で見る。
    ("__行数__", VIOL, "\n" + v_row(1), "\n  " + v_row(1), "行頭に空白を入れる"),
    ("違反ログの区分が不正", VIOL) + v_set(3, 3, "中程度")
    + ("区分を別の語にして集計から外す",),
    ("違反ログのタグが表に無い", VIOL) + v_set(0, 2, "うっかり見落とし")
    + ("タグを新造して2回目判定を外す",),
    ("監査に足したチェックが実在しない", VIOL) + v_set(0, 7, "足した(ちゃんと対応済み)")
    + ("実在しないチェック名を書く",),
    ("監査に足した根拠が書式外", VIOL) + v_set(0, 7, "足した")
    + ("「足した」とだけ書いて停止を解除する",),
    # --- 第3回レッドチームで実際に抜けられた形 ---
    ("違反ログの行を解釈できない", VIOL, v_row(2), "\n" + v_row(2),
     "表の途中に空行を入れて以降を消す"),
    ("違反ログのタグが空", VIOL) + v_set(0, 2, "")
    + ("タグ欄を空にして2回目判定から外す",),
    ("監査に足したチェックが実在しない", VIOL) + v_set(0, 7, "足した(e)")
    + ("1文字で実在照合を通す(部分一致)",),
    ("足したチェックに自己テストが無い", VIOL) + v_set(0, 7, "足した(categoryLinks漏れ)")
    + ("注入ケースの無い種別を「足した」と書く",),
    ("違反ログの新しい行が錠前に無い", VIOL) + v_set(-1, 0, "2026-08-14")
    + ("錠前に取り込まれていない行を足す",),
    ("違反ログの行が消えた", VIOL) + v_set(1, 0, "2026-08-14")
    + ("過去行の日付を書き換えて別物にする",),
    ("PreToolUseの配線が消えた", ".claude/settings.json",
     "no_heredoc_backslash.py", "no_heredoc_DISABLED.py",
     "T-01フックの登録を外す"),
    ("CIの検査が抜けている", ".github/workflows/rules.yml",
     "run: python tools/lock.py", "run: echo 錠前の検査は省略",
     "CIから錠前の検査を外す"),
    # --- 第4回レッドチームで実際に抜けられた形 ---
    ("CIが失敗しても止まらない", ".github/workflows/rules.yml",
     "run: python tools/lock.py", "run: python tools/lock.py || true",
     "CIのステップに || true を足す"),
    ("PreToolUseの配線が消えた", ".claude/settings.json",
     '"PreToolUse"', '"PostToolUse"',
     "PreToolUseをPostToolUseに改名する"),
    ("PreToolUseがBashを見ていない", ".claude/settings.json",
     '"matcher": "Bash|PowerShell"', '"matcher": "__off__"',
     "matcherを実在しないツール名にする"),
    ("PreToolUseの設定が壊れている", ".claude/settings.json",
     '"permissions": {', '"permissions" {',
     "設定JSONを壊して検査を諦めさせる"),
    ("保護を外す操作の見張りが消えた", ".claude/settings.json",
     "no_protection_bypass.py", "no_protection_DISABLED.py",
     "ブランチ保護を外す操作の見張りを外す"),
    ("監査チェックが消えた", "tools/audit_characters.py",
     'add("重複登録", "HIGH", "skills.html に同名スキルが複数: " + n)',
     'pass  # add("重複登録", "HIGH", ...) 相当',
     "チェックを消して名前をコメントに残す"),
    ("門番の中身が変わった", "tools/precommit_check.py",
     "sys.stdout.reconfigure(encoding=\"utf-8\")",
     "sys.stdout.reconfigure(encoding=\"utf-8\")\n# テスト",
     "門番スクリプトを書き換える"),
    ("門番名だけで済ませている", VIOL) + v_set(0, 7, "足した(lock)")
    + ("門番名だけを書いて2回目の停止を外す",),
    # I-01の対策そのもの。4体のレッドチームが全員ここを突いた(F-7/G-1/H-1/I-2)。
    # 未確認の段を作ったうえで、調査ログに「手書きの」記録を2件足す。
    # 証拠(HTTPの取得結果)が無い記録は数えないので、HIGHは消えないはず。
    ("未確認の根拠なし", "characters-kyoku.html",
     '{level:"TR5", points:"200", effect:null},\n        {level:"TR6", points:"パラレル", effect:null}\n'
     '      ],\n      // 合成テーブルはixanaryスキルページ「百識ノ計」',
     '{level:"TR5", points:"200", effect:"テスト値"},\n        {level:"TR6", points:"パラレル", effect:null}\n'
     '      ],\n      // 合成テーブルはixanaryスキルページ「百識ノ計」',
     "調査ログにでっち上げた取得記録2件を足して黙らせる(第7回 CC-1)",
     # アンカーはJSONの開き括弧だけにする。中のキー名を目印にすると、
     # 調査ログに項目が増えた日に外れる(I-14と同じ形。実際CIで外れた)。
     ("tools/research_log.json", '{\n "',
      '{\n "TR:百識ノ計": [\n'
      '  {"date": "2026-08-13", "evidence": {"status": 200, "bytes": 41234, "sha256": "0000000000000000"}, "found": false,\n'
      '   "result": "手書き(自己テスト用)", "source": "ixanary", "url": "https://example.invalid/1"},\n'
      '  {"date": "2026-08-13", "evidence": {"status": 200, "bytes": 41234, "sha256": "0000000000000000"}, "found": false,\n'
      '   "result": "手書き(自己テスト用)", "source": "ixawiki", "url": "https://example.invalid/2"}\n'
      ' ],\n "')),
    ("CIが失敗しても止まらない", ".github/workflows/rules.yml",
     "      - name: ルール索引と違反ログの整合",
     "      - name: ルール索引と違反ログの整合\n        continue-on-error: true",
     "CIを赤でも通るようにする"),
    # --- 第5回レッドチームで実際に抜けられた形 ---
    # W-14 の再発。索引(I-02)だけ直して、手順の原文を旧ソースのままにする。
    # 注記(「使わない」等)が同じ行にあれば通すので、注記の無い行を選ぶ。
    ("廃止した情報源が手順に残っている", ".claude/agents/kanshi-yaku.md",
     "- ixanary.comはレベル/鍛錬(TR)別の数値データが",
     "- 45do.infoはレベル/鍛錬(TR)別の数値データが",
     "索引を直したのに手順の原文が旧ソースを指したまま"),
    # S-4/T-5/Y-6: known_checks() が正規表現だったので、コメントに1行書くだけで
    # 「実在するチェック種別」を捏造できた。AST に変えた後は捏造できない。
    ("監査に足したチェックが実在しない", VIOL) + v_set(0, 7, "足した(架空の種別)")
    + ("コメントに種別名だけ書いて実在照合を通す",
       ("tools/audit_characters.py", "# -*- coding: utf-8 -*-",
        '# -*- coding: utf-8 -*-\n# add("架空の種別", "HIGH", "コメントに書いただけ")')),
    # T-4/V-4/AA-6: Write/Edit が見張りの外だったので、コマンドを1つも実行せずに
    # `.git/hooks/pre-commit` を上書きできた。その見張りの配線が消えたら鳴るか。
    (".git直接書き換えの見張りが消えた", ".claude/settings.json",
     "no_git_internal_write.py", "no_git_internal_DISABLED.py",
     ".git/ の直接書き換えの見張りを外す"),
    (".git直接書き換えの見張りが消えた", ".claude/settings.json",
     '"matcher": "Write|Edit|NotebookEdit"', '"matcher": "NotebookEdit"',
     "matcher から Write/Edit を外す"),
    # AA-5: 個人設定(gitignore済み=差分に出ない)は誰も見ていなかった。
    # 後勝ちなので、ここに hooks を書けば共有側の門番を丸ごと無効化できた。
    # 元から無いこともあるファイルなので、old=None(作る)で試す。
    ("個人設定が門番を上書きしている", ".claude/settings.local.json", None,
     '{\n  "hooks": {\n    "PreToolUse": []\n  }\n}\n',
     "個人設定で共有側の門番を空にする"),
    ("個人設定が壊れている", ".claude/settings.local.json", None,
     '{ "permissions": ',
     "個人設定を壊して検査を諦めさせる"),
    # R-01〜R-04(2026-08-13): 攻撃側を縛る仕組み。回が開いている間しか効かないので、
    # 配線が外れても普段は何も起きない = 気づけない。外れたら鳴ることを確かめる。
    ("レッドチームの見張りが消えた", ".claude/settings.json",
     "no_redteam_write.py", "no_redteam_DISABLED.py",
     "攻撃側の見張りを外す"),
    ("レッドチームの回が閉じていない", "docs/redteam-log.txt", "@@append@@",
     "START\t2026-08-13 00:00:00\tdeadbeef1234\t架空の回\t1 files\tc:/temp\tdead\n",
     "回を開いたまま閉じない(本物が無傷か誰も確かめていない)"),
    # BD-2(第6回): 記録の中身は誰も守っていなかった。過去行を消せば全部消えた。
    ("レッドチームの記録が書き換えられた", "docs/redteam-log.txt",
     "# レッドチームの回の開閉記録", "# (書き換えた)",
     "回の履歴を消すために過去行を書き換える"),
    ("レッドチームの回を中断した", "docs/redteam-log.txt", "@@append@@",
     "START\t2026-08-13 00:00:00\tdeadbeef1234\t架空の回\t1 files\tc:/temp\tdead\n"
     "ABORT\t2026-08-13 00:00:01\tdeadbeef1234\t架空の回\t自己テスト用の中断\n",
     "中断の痕跡が報され続けるか"),
    ("レッドチームが本物を触った", "docs/redteam-log.txt", "@@append@@",
     "END-FAILED\t2026-08-13 00:00:00\tdeadbeef1234\t架空の回\n",
     "本物を触ったまま回を閉じようとした記録を握りつぶす"),
    # R-6/S-6: エージェント定義が門番の見張りの外だった。
    # `.claude/agents/` の未ステージ改変で止まるかは gate_selftest 側で見る。
    # X-5: 素材を1行壊すと、そこから先の約30種別がまるごと黙った。
    # 区画ごとに囲ったので、壊れた区画だけが指摘になる。
    ("ルール検査が例外で落ちた", "docs/RULES.md",
     "\n最終棚卸し: ", "\n最終棚卸し: 2026-99-99\n元の記録: ",
     "棚卸し日を壊して以降の検査を全部黙らせる"),
]


def violation_rows():
    """違反ログから読み取れた行数。書式を崩して行を隠せないかを見るのに使う。"""
    r = subprocess.run([sys.executable, "-c",
                        "import sys;sys.path.insert(0,'tools');import rules;"
                        "print(len([x for x in rules.violations() "
                        "if not x['parse_error']]))"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    try:
        return int((r.stdout or "").strip())
    except ValueError:
        return -1


def audit():
    """種別ごとの件数。集合(あるか無いか)で見ると、元から出ている種別を検証できない。

    2026-08-12(第2回レッドチーム対応前の自己点検):
    集合で比べていたため「S以上でページ無し」と「武将名の表記ゆれ」が
    『元から出ているため判定不能』になり、自己テストが恒常的に赤だった。
    赤が続くと見なくなるので、件数で比べて増えたかどうかを見る。
    """
    subprocess.run([sys.executable, os.path.join("tools", "audit_characters.py")],
                   cwd=ROOT, capture_output=True)
    with io.open(FINDINGS, encoding="utf-8") as f:
        return collections.Counter(x["cat"] for x in json.load(f))


def main():
    # E-16: 以前は本物の作業ツリーを書き換えながら走っていた。強制終了すると
    # 門番フックの正本や違反ログが改変されたまま残り、案内どおり
    # `install_hooks.py` を実行すると**壊れた正本が .git/hooks に複製されて緑になった**。
    # どんな状態で中断されても元に戻せるよう、走る前にツリーがきれいであることを要求する。
    st = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                        capture_output=True, text=True, encoding="utf-8").stdout.strip()
    if st and "--force" not in sys.argv:
        print("[停止] 作業ツリーに未コミットの変更がある。")
        print("この自己テストは実ファイルに違反を注入して復元する。途中で落ちると")
        print("注入が残り、それが正本として焼き付く(第2回レッドチーム E-16)。")
        print("先にコミットするか退避してから実行する。承知のうえなら --force。")
        for l in st.split("\n")[:10]:
            print("  " + l)
        return 1

    base = audit()
    base_rows = violation_rows()
    print("注入前: %d種別 / 合計%d件 / 違反ログ%d行\n"
          % (len(base), sum(base.values()), base_rows))
    ok = ng = skip = 0
    tmp = tempfile.mkdtemp()
    for cat, rel, old, new in CASES:
        path = os.path.join(ROOT, rel)
        src = io.open(path, encoding="utf-8", newline="").read()
        if old not in src:
            print("  skip %-22s (注入位置が見つからない)" % cat)
            skip += 1
            continue
        bak = os.path.join(tmp, rel.replace("/", "_").replace("\\", "_"))
        shutil.copy2(path, bak)
        try:
            io.open(path, "w", encoding="utf-8", newline="").write(src.replace(old, new, 1))
            got = audit()
            if got[cat] > base[cat]:
                print("  OK   %-22s 検出した(%d件 → %d件)" % (cat, base[cat], got[cat]))
                ok += 1
            else:
                print("  NG   %-22s 違反を入れても増えない(%d件のまま)" % (cat, got[cat]))
                ng += 1
        finally:
            shutil.copy2(bak, path)
    # E-15: 「違反を入れたら鳴るか」だけでなく「黙らせる書き方をしても鳴るか」を見る。
    # 検査を回避する形は、違反そのものより見つけにくい。
    print("\n-- 検査を黙らせようとしたときに、ちゃんと鳴るか --")
    for ev in EVASIONS:
        cat, rel, old, new, why = ev[:5]
        # 2つのファイルを同時に触らないと再現できない回避もある
        # (例: 未確認の段を作る + 調査ログに手書きの記録を足す)
        extra = ev[5] if len(ev) > 5 else None
        edits = [(rel, old, new)] + ([extra] if extra else [])
        srcs, missing, created = {}, False, []
        for r, o, _n in edits:
            p = os.path.join(ROOT, r)
            # old が None のケースは「そのファイルを作る」。gitignore されていて
            # 元から存在しないもの(.claude/settings.local.json)を試すために要る。
            if o == "@@append@@":
                srcs[r] = io.open(p, encoding="utf-8", newline="").read() \
                    if os.path.exists(p) else ""
                continue
            if o is None:
                srcs[r] = "" if not os.path.exists(p) else \
                    io.open(p, encoding="utf-8", newline="").read()
                if not os.path.exists(p):
                    created.append(r)
                continue
            if not os.path.exists(p):
                missing = True
                continue
            srcs[r] = io.open(p, encoding="utf-8", newline="").read()
            if o not in srcs[r]:
                missing = True
        if missing:
            print("  skip %-30s (注入位置が見つからない)" % why)
            skip += 1
            continue
        baks = {}
        for r in srcs:
            if r in created:
                continue
            b = os.path.join(tmp, "ev_" + r.replace("/", "_").replace("\\", "_"))
            shutil.copy2(os.path.join(ROOT, r), b)
            baks[r] = b
        path = os.path.join(ROOT, rel)
        src = srcs[rel]
        try:
            for r, o, n in edits:
                io.open(os.path.join(ROOT, r), "w", encoding="utf-8",
                        newline="").write(
                            n if o is None else
                            (srcs[r] + n) if o == "@@append@@" else
                            srcs[r].replace(o, n, 1))
            if cat == "__行数__":
                # 「指摘が増えるか」ではなく「行が数え落とされないか」を見るケース
                got_rows = violation_rows()
                if got_rows == base_rows:
                    print("  OK   %-30s 行数が変わらない(%d件)" % (why, got_rows))
                    ok += 1
                else:
                    print("  NG   %-30s 行が消えた(%d件 → %d件)" % (why, base_rows, got_rows))
                    ng += 1
                continue
            got = audit()
            if got[cat] > base[cat]:
                print("  OK   %-30s 鳴った(%s)" % (why, cat))
                ok += 1
            else:
                print("  NG   %-30s 黙らせられた(%s が %d件のまま)" % (why, cat, got[cat]))
                ng += 1
        finally:
            for r, b in baks.items():
                shutil.copy2(b, os.path.join(ROOT, r))
            for r in created:            # 元から無かったものは消して戻す
                try:
                    os.remove(os.path.join(ROOT, r))
                except OSError:
                    pass

    print("\n検出できた %d / 検出できず %d / 注入位置が無い %d" % (ok, ng, skip))
    after = audit()
    same = after == base
    print("復元後の件数が元と同じ:", same)

    # A-8(2026-08-12レッドチーム指摘): skipを成功扱いにすると、データが変わって
    # 注入位置が見つからなくなったときに「0 OK / 0 NG / 全部skip」で合格に見えてしまう。
    # skipは失敗として扱い、直すべき場所を出す。
    if skip:
        print("\n[失敗] 注入位置が見つからないケースが %d件。" % skip)
        print("  CASES の置換文字列を今のデータに合わせる。")

    # 自己テストが用意されていないチェック種別を可視化する
    #
    # E-17(2026-08-12 第2回レッドチーム指摘): 以前は audit_characters.py の
    # `add("...")` という書き方だけを数えていた。rules.py 由来のチェックは
    # `add(cat, sev, msg)` の1行で橋渡ししているので**まるごと会計の外**にあり、
    # そこに新しいチェックを足しても「自己テストが無い」に現れなかった。
    # また set の `.add("初期:%s")` を拾って、存在しない種別を2件表示していた。
    covered = {c for c, _f, _o, _n in CASES} | {ev[0] for ev in EVASIONS}
    # J-1(第4回): テキストの正規表現だと、チェックを消して名前をコメントに残すだけで
    # 「消えていない」ことになった。錠前と同じ AST ベースの収集を使う。
    try:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import lock as _lock
        _lock.ROOT = ROOT
        known = _lock.check_names()
    except Exception as e:
        print("\n[注意] 種別を数えられない: %s" % e)
        known = set()
    missing = sorted(known - covered)
    print("\n自己テストが無いチェック種別: %d件" % len(missing))
    for x in missing:
        print("   " + x)

    # G-8/F-12(2026-08-13 第3回): 未カバーの一覧は印字するだけで終了コードに影響せず、
    # しかも covered も known も実装から動的に作っているので、
    # **チェックを丸ごと消すと分母からも消えて緑のまま**だった(S-01の再演)。
    # 上限を置いて、増えたら赤にする。減るのは歓迎なので自動で締める。
    cap_path = os.path.join(ROOT, "tools", "selftest_uncovered.txt")
    cap = None
    if os.path.exists(cap_path):
        for line in io.open(cap_path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                cap = int(line.split()[0])
                break
    if cap is None:
        io.open(cap_path, "w", encoding="utf-8", newline="\n").write(
            "# 自己テストの注入ケースが無いチェック種別の上限。\n"
            "# 増えたら赤にする(新しいチェックを無検査で足させないため)。\n"
            "# 減らすのは歓迎。減ったら自動でここも締まる。\n"
            "%d\n" % len(missing))
        print("上限を %d件として tools/selftest_uncovered.txt に記録した。" % len(missing))
    elif len(missing) > cap:
        print("\n[失敗] 未カバーが上限 %d件 を超えて %d件になった。" % (cap, len(missing)))
        print("  新しく足したチェックには CASES / EVASIONS に注入ケースを置く。")
        ng += 1
    elif len(missing) < cap:
        io.open(cap_path, "w", encoding="utf-8", newline="\n").write(
            "# 自己テストの注入ケースが無いチェック種別の上限。\n"
            "# 増えたら赤にする(新しいチェックを無検査で足させないため)。\n"
            "# 減らすのは歓迎。減ったら自動でここも締まる。\n"
            "%d\n" % len(missing))
        print("上限を %d → %d件 に締めた。" % (cap, len(missing)))

    return 1 if (ng or skip or not same) else 0



# Q-6(2026-08-13 第4回レッドチーム): モジュール末尾で走らせていたので、
# import した瞬間に走って SystemExit していた。テストや別のツールから読めるようにする。
if __name__ == "__main__":
    sys.exit(main())
