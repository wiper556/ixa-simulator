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


# 注入先について(2026-08-14)
#
# 監査が読むのは正本 data/busho*/{No}.json ・ data/skill/{名前}.json になった。
# 一覧ページの配列は「一覧に要るフィールドだけ」の生成物なので、
# 鍛錬表・合成表・スキル本文はもうページ側に無い。
# データの中身を試すケースは、ページではなく**正本のJSONに注入する**。
#
# JSONは json.dump(indent=1) で書かれているので、キーは3スペース字下げ、
# 配列の要素の `{` は2スペース。目印はその形に合わせてある。
# 使う武将/スキルは固定:
#   data/busho/1321.json        六角定頼(初期スキル 天弦ノ威軍[S])
#   data/busho-kyoku/2614.json  佐渡島方治(初期スキル 百識ノ計)
#   data/skill/月詠ノ覇威.json   佐渡島方治をS1枠の所持武将に持つ

BUSHO = "data/busho/1321.json"
# 2026-08-14: 極を通常極とプラチナ・シークレットに分けたとき、2614(佐渡島方治)は
# カードNo.の100の位が6=プラチナ極なので data/busho-kyoku-ps/ へ移った。
# ここのパスが古いままでCIが落ちた(FileNotFoundError)。
KYOKU = "data/busho-kyoku-ps/2614.json"
SKILL = "data/skill/月詠ノ覇威.json"

# (チェック種別, 触るファイル, 置換前, 置換後)
CASES = [
    # E-14(2026-08-12 第2回レッドチーム指摘): 1ルールに1ケースだと、
    # そのルールの**別の分岐**を丸ごと消しても緑のままになる。
    # 実際、監査から「合成候補の走査」を削除(=違反S-01そのものの再現)しても
    # 19/19 OK・exit 0 で通った。分岐ごとに1ケース置く。
    ("S以上でページ無し", BUSHO,
     # 分岐1: 初期スキル(Aランクは規約上ページ不要なので対象外になる。Sで試す)
     '"initialSkill": "天弦ノ威軍"', '"initialSkill": "存在しない架空スキルS"'),
    ("S以上でページ無し", BUSHO,
     # 分岐2: 合成候補。S-01はこちらを数えていなかったのが原因だった。
     '"skill": "天弦ノ威軍",\n   "rank": "S"',
     '"skill": "存在しない架空の合成候補SS",\n   "rank": "S"'),
    ("sourceCharactersのdb", SKILL,
     '"name": "佐渡島方治",\n   "no": "2614",\n   "slot": "S1",\n   "db": "kyoku",',
     '"name": "佐渡島方治",\n   "no": "2614",\n   "slot": "S1",'),
    # 段飛びは「段の名前が連続しているか」だけを見ている。
    # TR2 を表に無い段名にすると LV10/TR1/TR3〜TR6 になり、間が空く。
    ("trTableの段飛び", KYOKU,
     '"level": "TR2"', '"level": "TR9"'),
    ("シミュのcost未設定", "assets/js/ixa-data.js",
     "no:'2614', cost: 3,", "no:'2614',"),
    # 2026-08-16: シミュレーターの名前が正本とずれる。(N)の振り直しで26件、
    # 綴り間違いで9件出た。specialSkills も同じ綴りだと計算は通ってしまう。
    ("シミュの名前が正本と違う", "assets/js/ixa-data.js",
     "{ name:'佐渡島方治', no:'2614'", "{ name:'佐渡島方治（9）', no:'2614'"),
    ("シミュの名前が正本と違う", "assets/js/ixa-data.js",
     "ki:'S'}, initialSkill:'百識ノ計'",
     "ki:'S'}, initialSkill:'百識ノ討'"),
    # 2026-08-15: 効果文の数値の前に半角スペースを入れて統一したので、
    # 注入の目印も「攻撃 390%上昇」に合わせた(合わないと注入できず skip になる)
    ("effectShortの接頭辞", KYOKU,
     '"effectShort": "攻撃 390%上昇+防御 390%上昇+部隊内卓越追加確率+25%',
     '"effectShort": "100% / 効果 攻撃 390%上昇+防御 390%上昇+部隊内卓越追加確率+25%'),
    ("ドット付きランク", KYOKU,
     '"yari": "B",', '"yari": ".B",'),
    # 2026-08-24: S-15「スキル→武将」の噛み合わせ。逆引きは武将→スキルしか
    # 見ておらず、スキル側に書いてある持ち主が本当にそのスキルを持っているかを
    # 誰も検査していなかった(実際に7件の誤りが残っていた)。分岐ごとに1ケース。
    ("スキルの持ち主が正本に無い", SKILL,
     # 分岐1: 存在しないカード番号を書く
     '"name": "伊達政宗（6）【覇】",\n   "no": "1226",',
     '"name": "伊達政宗（6）【覇】",\n   "no": "999999",'),
    ("スキルの持ち主が噛み合わない", SKILL,
     # 分岐2: 実在する武将だが、そのスキルを持っていない枠を書く
     # (No.1226 の A枠は「遠呂智ノ閃光」で月詠ノ覇威ではない)
     '"no": "1226",\n   "slot": "S1"',
     '"no": "1226",\n   "slot": "A"'),
    # 2026-08-24: S-16 武将側とスキル側で LV10 の効果が食い違う。
    # 94件の食い違いのうち3件はスキル側が効果ごと落としていた。
    # 落ちている向きが2つあるので、それぞれ1ケース置く。
    ("スキル側が効果を落としている", KYOKU,
     # 武将側に効果を足す = スキル側に無い、という形にする
     '"effect": "弓・器・焙　確率 100% / 攻撃 390%上昇+防御 390%上昇',
     '"effect": "弓・器・焙　確率 100% / 攻撃 390%上昇+防御 390%上昇+速度 77%上昇'),
    ("武将側が効果を落としている", KYOKU,
     # 武将側から防御を落とす = スキル側にあるのに武将側に無い、という形
     '"effect": "弓・器・焙　確率 100% / 攻撃 390%上昇+防御 390%上昇',
     '"effect": "弓・器・焙　確率 100% / 攻撃 390%上昇'),
    ("slotの独自語", SKILL,
     '"name": "佐渡島方治",\n   "no": "2614",\n   "slot": "S1",',
     '"name": "佐渡島方治",\n   "no": "2614",\n   "slot": "候補",'),
    # 2026-08-16: 合成表では枠があるのにスキル側が「移植不可」になっていた
    # (No.1279 荒木村重（2）の魔導禁鎖)。26件あり、うち5件は「移植不可なのに
    # 実は移植できる」という使い方を間違えさせる誤りだった。
    # 2026-08-16: ここは武将名を目印にしていたので、（N）の振り直しで
    # 「上杉謙信（2）【覇】」が「（6）」になった日に注入位置を見失って赤くなった。
    # 名前は運用中に普通に変わる。**カードNo.で指す。**
    ("slotが実枠と違う", SKILL,
     '"no": "1238",\n   "slot": "A"',
     '"no": "1238",\n   "slot": "移植不可"'),
    # 2026-08-16に足した検査2つ。
    # 波濤ノ剛撃は3体が50%で揃っている。2614だけずらせば「武将により違う」になる。
    # (2614.json の中で "rate": "50%" はC枠の1箇所だけ)
    ("確率が武将により違う", KYOKU,
     '"rate": "50%",', '"rate": "45%",'),
    # 同じ極の中に居る別カード(2582 独眼竜政宗)と完全同名にする
    ("同じレアリティに同名", KYOKU,
     '"name": "佐渡島方治",\n "no": "2614",',
     '"name": "独眼竜政宗",\n "no": "2614",'),
    # V-07(2026-08-13): 確率の「+」を1件書き戻して、見張りが鳴るか。
    ("確率に+が付いている", KYOKU,
     '"rate": "100%",', '"rate": "+100%",'),
    ("武将名の表記ゆれ", KYOKU,
     '"name": "佐渡島方治",\n "no": "2614",', '"name": "佐渡島方治(2)",\n "no": "2614",'),
    ("データ内のHTMLタグ", KYOKU,
     '"effect": "部隊内武将の全スキルの卓越追加確率+25%',
     '"effect": "<span style=\\"color:red\\">部隊内</span>武将の全スキルの卓越追加確率+25%'),
    ("模倣不可の位置", KYOKU,
     # 消すと「模倣不可が無い」扱いで対象外になるので、①より後ろへ移す
     '対象:弓・器・焙\\n模倣不可\\n①攻撃 390%上昇する',
     '対象:弓・器・焙\\n①攻撃 390%上昇する\\n模倣不可'),
    # 2026-08-14: 正本は data/ に移り、ページの配列はそこからの生成物になった。
    # 監査は正本を読むので、**ページ側だけ**を書き換えられると
    # 「監査は青丸・公開ページは赤丸」という食い違いが起きる。
    # N-1(第4回レッドチーム)そのものの手口を注入して、突き合わせが働くか見る。
    ("ページの配列が正本と違う", "characters.html",
     "  // BUILD:generals:end",
     "  generals.forEach(function(g){ if (g.no === '1315') { g.approved = true; } });\n"
     "  // BUILD:generals:end"),
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

    # TR5 に値を入れると、その上の TR1〜TR4 が「未確認」表示になる。
    # 調べた記録が2件以上ないとHIGHになるはず。
    ("未確認の根拠なし", KYOKU,
     '"level": "TR5",\n   "points": "200",\n   "effect": null',
     '"level": "TR5",\n   "points": "200",\n   "effect": "テスト値"'),
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
    # --- 門番の対(S-14が2回目になったので機械で見る。2026-08-19) ---
    # 実際に起きた形をそのまま注入する。1つ目は CI だけ許可記録を見なくする
    # (8b69e213 の状態)。2つ目は自己テストの筋書きが候補を選べない状態
    # (No.1310 が正式な赤丸になって D-14 が空振りした状態)。
    ("門番の対がずれている", ".github/workflows/rules.yml",
     'allowed = approvals_of(".")', "allowed = {}",
     "CIの赤丸ゲートだけ許可記録を見なくする"),
    ("門番の対がずれている", "tools/gate_selftest.py",
     'os.path.join(repo, "data", "busho", no + ".json")',
     'os.path.join(repo, "data", "bushoX", no + ".json")',
     "赤丸の自己テストが筋書きに使うカードを選べない状態にする"),
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
    ("未確認の根拠なし", KYOKU,
     '"level": "TR5",\n   "points": "200",\n   "effect": null',
     '"level": "TR5",\n   "points": "200",\n   "effect": "テスト値"',
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
    # EL-1(第10回、高): 錠前ファイルから守り先のキーを1行消すと、
    # そのファイルは無検査になった(30本まるごと消しても「一致している」)。
    ("錠前から守り先が抜けている", "tools/checks.lock",
     '"tools/audit_characters.py":', '"tools/audit_characters_X.py":',
     "錠前から守り先を1行消して無検査にする"),
    # 2026-08-14: 生成ブロックの中を手で書き換える形。
    # 監査が読むのは正本なので、ここを見ていないと公開ページだけ赤丸にできる。
    ("ページの配列が正本と違う", "characters.html",
     'no:"1315"', 'no:"1315", approved:true',
     "生成ブロックの中を手で書き換えて赤丸を1体足す"),
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
    # 2026-08-13: 中断の指摘を閉じる ABORT-CLOSED を足した。指摘を消す力がある
    # 記録なので、宛先が実在しないものを書いて黙らせられないことを試す。
    ("レッドチームの始末の記録が宙に浮いている", "docs/redteam-log.txt", "@@append@@",
     "ABORT-CLOSED\t2026-08-13 00:00:02\t2020-01-01 00:00:00\t実在しない回に始末をつけたことにする\n",
     "存在しない中断に始末の記録を書いて指摘を消しにいく"),
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
