# 未登録武将の一括登録スクリプト

2026-08-14に、登録待ち238体を片付けるために作ったもの。6体登録して形が固まった。
**全自動ではない。** 統率の目視確認と、効果文の書式の目視確認は人がやる。

## 流れ

```
1. python tools/register/regqueue.py            登録待ちを並べる(No.降順)
2. python tools/register/badge_grid.py 7401 ... 統率バッジを1枚に並べる → 目で読む
3. python tools/register/regfetch.py 7401 ...   標準2ソースから取って _work/draft_{No}.json
4. python tools/register/regwrite.py 7401 ...   下書きを仕上げて data/busho*/{No}.json に書く
5. python tools/register/skillbuild.py 名前:S   S以上で未作成のスキルページを作る
6. python tools/register/fill_short.py 7401 ... 合成候補の効果文を埋める
7. python tools/register/wireup.py 7401 ...     逆引き・LINKED_SKILLS・ownHiddenCandidate
                                                + 一覧ページの逆引き(S-06)
8. python tools/build_data.py → prerender.py → gen_detail_pages.py
9. python tools/audit_characters.py             残った不整合を拾う
```

`_work/` は下書き置き場。gitには入れない。

**手順7の S-06 だけは引数の武将に限らず全ページを見る。** 一覧ページ
(`skills-*.html`)は`skills.html`と違って生成物ではなく`sourceCharacters`を
独自に複製しているので、取りこぼすと監査の「一覧の逆引き同期漏れ」で鳴る。
`python tools/register/wireup.py`(引数なし)で同期だけを流せる。
足すだけで並べ替えや削除はしない。

## 人がやること(自動化していない)

- **統率(rankGrades)はカード画像のバッジを目で読む(D-01)。** 手順2で並べた画像を見て、
  手順3が取ってきたixawikiのカードページの値と一致することを確かめる。
  ixawikiの**武将カード一覧のほうは弓と馬の列が入れ替わって見える**ことがある
  (No.10014で実際に食い違った)。カードページ側とカード画像を正とする。
- 赤い宝石が乗ったバッジはS/SSと読まずX以上として扱う(D-02)。判別できなければ聞く。
- 効果文が書式どおりか(F-01〜F-06)を目で見る。下の「間違えたところ」を参照。
- `reviewedOk`(黄丸)を付けるかどうかの判断。`approved`(赤丸)は**絶対に付けない**(D-14)。

## 間違えたところ(同じ穴に落ちないために)

2026-08-14の6体で、監査に3回止められた。

1. **ixanaryの生表記をそのまま入れた。** 「防御：580%上昇」はうちの書式では
   「防御 580%上昇」。`skillbuild.parse_level` で全角コロンを外している。
2. **対象の切り出しが効果文まで飲み込んだ。** 「対象 合流 銅銭獲得率：2.5%」から
   対象を取ると「合流 銅銭獲得率：2.5%」になっていた。「〜：」が出たら打ち切る。
   キーワード(無尽・撤退・飛翔n)は対象の一部なので残す。
3. **S-08(ownHiddenCandidate)を取り違えた。** 「そのスキル自身のページのS1枠の2次」
   ではなく、**「そのスキルが1次候補として載っている枠の2次」**。
   つまり武将側の`afterSkill`と一致する。監査が食い違いで止めてくれた。

## 情報源の食い違い(実例)

- **No.7401 山田長政のコスト**: ixanaryだけ0、カード画像・ixawikiのカードページ・
  極一覧はすべて1。カード画像を正とした。
- **No.7402 蜂須賀家政の指揮兵数**: ixanaryの成長表の突破ランク別の値は+0.5版(B-01)。
  ★0-0の値だけを`lv0Troops`に入れる。

## 章について

ixanary・ixawikiのカードページ・一覧のどこにも章の記載が無い。
登録済みの章はユーザーがゲーム内で確認したもの。**カード番号から外挿してはいけない**
(2xxx番台と7xxx番台が混ざって1章になる)。分からなければ`"未確認"`のままにする。
