# -*- coding: utf-8 -*-
"""監査チェックの自己テスト。「0件」が本当に健全なのか、検査が動いていないだけなのかを分ける。

なぜ要るか(docs/RULE-OPERATION.md「チェックを足すときの義務」):
2026-08-12の違反S-01は、自分で書いた検査がルールより狭いまま自分で合格判定したのが原因だった。
検査が0件を返しても、それが「不備が無い」なのか「検査が動いていない」なのかは区別できない。
そこで**わざと違反を作って、そのチェックが拾うかどうか**を確かめる。

やること: 対象ファイルを退避 → 違反を1つ注入 → 監査を走らせる → 該当種別が出るか確認 → 復元。

    python tools/audit_selftest.py
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FINDINGS = os.path.join(ROOT, "tools", "audit_out", "findings.json")

# (チェック種別, 触るファイル, 置換前, 置換後)
CASES = [
    ("S以上でページ無し", "characters.html",
     # Sランクの初期スキルで試す(Aランクは規約上そもそもページ不要なので対象外になる)
     'initialSkill:"天弦ノ威軍"', 'initialSkill:"存在しない架空スキルS"'),
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
    ("未確認の根拠なし", "characters-kyoku.html",
     '{level:"TR5", points:"200", effect:null},\n        {level:"TR6", points:"パラレル", effect:null}\n'
     '      ],\n      // 合成テーブルはixanaryスキルページ「百識ノ計」',
     '{level:"TR5", points:"200", effect:"テスト値"},\n        {level:"TR6", points:"パラレル", effect:null}\n'
     '      ],\n      // 合成テーブルはixanaryスキルページ「百識ノ計」'),
]


def audit():
    subprocess.run([sys.executable, os.path.join("tools", "audit_characters.py")],
                   cwd=ROOT, capture_output=True)
    with io.open(FINDINGS, encoding="utf-8") as f:
        return {x["cat"] for x in json.load(f)}


def main():
    base = audit()
    print("注入前に出ている種別: %d\n" % len(base))
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
            if cat in got and cat not in base:
                print("  OK   %-22s 検出した" % cat)
                ok += 1
            elif cat in base:
                print("  --   %-22s 元から出ているため判定不能" % cat)
                skip += 1
            else:
                print("  NG   %-22s 違反を入れても検出しない" % cat)
                ng += 1
        finally:
            shutil.copy2(bak, path)
    print("\n検出できた %d / 検出できず %d / 判定不能・スキップ %d" % (ok, ng, skip))
    after = audit()
    print("復元後に出ている種別が元と同じ:", after == base)
    return 1 if ng else 0


sys.exit(main())
