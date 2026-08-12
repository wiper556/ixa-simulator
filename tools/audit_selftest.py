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
     "exec python tools/precommit_check.py --mode push",
     "exec python tools/precommit_check.py --mode push  # 正本を書き換えた"),
    # 索引に行を1つ足すと実数が増え、他文書の「N件の索引」という表記が古くなる。
    # 数字そのものを書き換える形にすると、ルールが増えるたびにこのケースが壊れる。
    ("ルール件数の表記ずれ", "docs/RULES.md",
     "| T-07 |", "| Z-01 | 自己テスト用の架空ルール | - | × | ✗ |\n| T-07 |"),
    ("棚卸しの期限切れ", "docs/RULES.md",
     "最終棚卸し: 2026-08-12", "最終棚卸し: 2020-01-01"),
    ("違反ログのIDが索引に無い", "docs/RULE-VIOLATIONS.md",
     "| 2026-08-12 | T-01 | 既知の罠を記憶で回避 |",
     "| 2026-08-12 | Z-99 | 既知の罠を記憶で回避 |"),
    # W-14(中)の監査欄を「まだ」に戻すと、未対応として数えられ、
    # 同時に根本原因「手順の自己改変」が2回目のまま未解決になる。
    ("違反ログに未対応が残っている", "docs/RULE-VIOLATIONS.md",
     "| 足した(pre-commit + ルール件数の表記ずれ) |", "| まだ |"),
    ("2回目の違反で作業停止中", "docs/RULE-VIOLATIONS.md",
     "| 足した(pre-commit) |", "| まだ |"),

    ("未確認の根拠なし", "characters-kyoku.html",
     '{level:"TR5", points:"200", effect:null},\n        {level:"TR6", points:"パラレル", effect:null}\n'
     '      ],\n      // 合成テーブルはixanaryスキルページ「百識ノ計」',
     '{level:"TR5", points:"200", effect:"テスト値"},\n        {level:"TR6", points:"パラレル", effect:null}\n'
     '      ],\n      // 合成テーブルはixanaryスキルページ「百識ノ計」'),
]


# 「検査を黙らせる書き方」。違反そのものより見つけにくいので、別枠で試す。
# (種別, 触るファイル, 置換前, 置換後, 何を試しているか)
EVASIONS = [
    ("違反ログの行を解釈できない", "docs/RULE-VIOLATIONS.md",
     "| 2026-08-12 | T-01 |", "| 2026/08/12 | T-01 |",
     "日付の区切りを / に変える"),
    ("違反ログの行を解釈できない", "docs/RULE-VIOLATIONS.md",
     "| 2026-08-12 | W-13 | 手順の自己改変 | 中 |",
     "| 2026-08-12 | W-13 | 手順の自己改変 |",
     "列を減らして行を短くする"),
    # 行頭の空白は正規化して読むので「解釈できない」にはならない。
    # 正しい期待は「その行が数え落とされないこと」なので、行数で見る。
    ("__行数__", "docs/RULE-VIOLATIONS.md",
     "\n| 2026-08-12 | S-01 |", "\n  | 2026-08-12 | S-01 |",
     "行頭に空白を入れる"),
    ("違反ログの区分が不正", "docs/RULE-VIOLATIONS.md",
     "| W-14 | 手順の自己改変 | 中 |", "| W-14 | 手順の自己改変 | 中程度 |",
     "区分を別の語にして集計から外す"),
    ("違反ログのタグが表に無い", "docs/RULE-VIOLATIONS.md",
     "| I-01 | 自己承認 |", "| I-01 | うっかり見落とし |",
     "タグを新造して2回目判定を外す"),
    ("監査に足したチェックが実在しない", "docs/RULE-VIOLATIONS.md",
     "| 足した(pre-commit) |", "| 足した(ちゃんと対応済み) |",
     "実在しないチェック名を書く"),
    ("監査に足した根拠が書式外", "docs/RULE-VIOLATIONS.md",
     "| 足した(PreToolUse) |", "| 足した |",
     "「足した」とだけ書いて停止を解除する"),
    # --- 第3回レッドチームで実際に抜けられた形 ---
    ("違反ログの行を解釈できない", "docs/RULE-VIOLATIONS.md",
     "| 2026-08-12 | W-13 |", "\n| 2026-08-12 | W-13 |",
     "表の途中に空行を入れて以降を消す"),
    ("違反ログのタグが空", "docs/RULE-VIOLATIONS.md",
     "| I-01 | 自己承認 |", "| I-01 |  |",
     "タグ欄を空にして2回目判定から外す"),
    ("監査に足したチェックが実在しない", "docs/RULE-VIOLATIONS.md",
     "| 足した(pre-commit) |", "| 足した(e) |",
     "1文字で実在照合を通す(部分一致)"),
    ("違反ログの行が消えた", "docs/RULE-VIOLATIONS.md",
     "| 2026-08-12 | T-01 | 既知の罠を記憶で回避 | 軽 |",
     "| 2026-08-13 | T-01 | 既知の罠を記憶で回避 | 軽 |",
     "過去行の日付を書き換えて別物にする"),
    ("PreToolUseの配線が消えた", ".claude/settings.json",
     "no_heredoc_backslash.py", "no_heredoc_DISABLED.py",
     "T-01フックの登録を外す"),
    ("CIの検査が抜けている", ".github/workflows/rules.yml",
     "run: python tools/lock.py", "run: echo 錠前の検査は省略",
     "CIから錠前の検査を外す"),
    ("CIが失敗しても止まらない", ".github/workflows/rules.yml",
     "      - name: ルール索引と違反ログの整合",
     "      - name: ルール索引と違反ログの整合\n        continue-on-error: true",
     "CIを赤でも通るようにする"),
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
    for cat, rel, old, new, why in EVASIONS:
        path = os.path.join(ROOT, rel)
        src = io.open(path, encoding="utf-8", newline="").read()
        if old not in src:
            print("  skip %-30s (注入位置が見つからない)" % why)
            skip += 1
            continue
        bak = os.path.join(tmp, "ev_" + rel.replace("/", "_").replace("\\", "_"))
        shutil.copy2(path, bak)
        try:
            io.open(path, "w", encoding="utf-8", newline="").write(src.replace(old, new, 1))
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
            shutil.copy2(bak, path)

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
    covered = {c for c, _f, _o, _n in CASES} | {c for c, _f, _o, _n, _w in EVASIONS}
    src = io.open(os.path.join(ROOT, "tools", "audit_characters.py"), encoding="utf-8").read()
    known = {x for x in re.findall(r'(?<![\w.])add\("([^"]+)"', src) if "%s" not in x}
    try:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import rules as _rules
        known |= set(re.findall(r'out\.append\(\("([^"]+)"',
                                io.open(_rules.__file__, encoding="utf-8").read()))
    except Exception as e:
        print("\n[注意] rules.py の種別を数えられない: %s" % e)
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


sys.exit(main())
