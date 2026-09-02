# -*- coding: utf-8 -*-
"""武将カードの読み取りに要る所を、1枚の拡大シートにまとめて出す。

なぜ要るか(2026-09-02):
新規登録では、カード画像から次を目で読む必要がある。
    武将名 / ふりがな / 職業 / レアリティ / コスト / 絵師
    攻撃・防御・兵法の初期値 / 指揮兵数 / 統率バッジ4つ
    スキル名 / ランク / 効果文
前回はこれを **6回に分けて拡大画像を作り、そのたびに見に行った。**
場所は毎回同じなので、1枚にまとめて1回で読めるようにする。

    python tools/register/cardsheet.py 2640 2859 7025 ...
        _work/sheet_{No}.png を書き出す(1体1枚)

    python tools/register/cardsheet.py --panel 2640 ...
        合成候補の画面(別スクリーンショット)も並べる場合。
        _work/panel_{No}.png に置いておくと拾う。

切り抜きと同じ「ハート基準」で位置を出すので、元スクリーンショットの
解像度がバッチごとに違っても同じ所が出る。
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image, ImageDraw           # noqa: E402
import crop_card as CC                     # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORK = os.path.join(ROOT, "tools", "register", "_work")

# (見出し, カード左上からの相対位置 x0,y0,x1,y1, 拡大率)
REGIONS = [
    ("レアリティ・コスト", (0, 0, 120, 62), 3.4),
    ("武将名・ふりがな・職業", (232, -2, 452, 32), 3.4),
    ("スキル(名前・ランク・効果文)", (230, 16, 466, 132), 3.4),
    ("統率バッジ(左上=槍 右上=馬 左下=弓 右下=器)", (134, 124, 226, 210), 4.0),
    ("攻撃・防御・兵法・指揮兵数", (146, 210, 226, 306), 3.4),
    ("絵師", (206, 292, 452, 318), 3.4),
]

# 見出しを日本語で書くための字体。無ければ既定のまま(四角に化けるが動きはする)
def _font():
    from PIL import ImageFont
    for p in (r"C:\Windows\Fonts\meiryo.ttc", r"C:\Windows\Fonts\YuGothM.ttc",
              r"C:\Windows\Fonts\msgothic.ttc"):
        try:
            return ImageFont.truetype(p, 15)
        except Exception:
            pass
    return None


def origin_of(im):
    """crop_card と同じやり方でカードの左上を出す。"""
    res = None
    for xl in (55, 40):
        res, _e = CC.find_heart(im, xl)
        if res and not (res[2] > 25 or res[3] > 25 or res[4] > 110):
            break
    ok = res and not (res[2] > 25 or res[3] > 25 or res[4] > 110)
    if ok:
        l, t = res[0] - CC.ANCHOR_X, res[1] - CC.ANCHOR_Y
        ok = l >= 0 and t >= 0
    if not ok:
        res, _e = CC.find_heart_bottom(im, 60)
        if not res:
            return None
        l, t = res[0] - CC.ANCHOR_X, res[1] - CC.ANCHOR_Y
        if l < 0 or t < 0:
            return None
    return (res[0] - CC.ANCHOR_X, res[1] - CC.ANCHOR_Y)


def sheet(no):
    src = CC.ARCH / ("スクリーンショット_%s.png" % no)
    if not src.exists():
        return "元スクリーンショットが無い: %s" % src.name
    im = Image.open(src).convert("RGB")
    W, H = im.size
    o = origin_of(im)
    if o is None:
        return ("No.%s カードの位置が出せない。crop_card.py --origin で"
                "位置を確かめてから使う" % no)
    L, T = o
    tiles = []
    for label, (x0, y0, x1, y1), z in REGIONS:
        box = (max(0, L + x0), max(0, T + y0),
               min(W, L + x1), min(H, T + y1))
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        c = im.crop(box)
        c = c.resize((int(c.width * z), int(c.height * z)), Image.LANCZOS)
        tiles.append((label, c))
    # 合成候補の画面があれば最後に足す
    pan = os.path.join(WORK, "panel_%s.png" % no)
    if os.path.exists(pan):
        p = Image.open(pan).convert("RGB")
        z = min(2.6, 1500.0 / max(1, p.width))
        tiles.append(("スキル追加合成の候補(1→A枠 2→B枠 3→C枠 隠し→S1枠 同一No→S2枠)",
                      p.resize((int(p.width * z), int(p.height * z)), Image.LANCZOS)))
    wid = max(t.width for _l, t in tiles) + 16
    hgt = sum(t.height + 30 for _l, t in tiles) + 34
    out = Image.new("RGB", (wid, hgt), "white")
    d = ImageDraw.Draw(out)
    fnt = _font()
    d.text((8, 6), "No.%s" % no, fill="black", font=fnt)
    y = 30
    for label, t in tiles:
        d.text((8, y), label, fill="black", font=fnt)
        out.paste(t, (8, y + 20))
        y += t.height + 30
    if not os.path.isdir(WORK):
        os.makedirs(WORK)
    p = os.path.join(WORK, "sheet_%s.png" % no)
    out.save(p)
    print("  No.%-6s → %s (%dx%d) カード左上=(%d,%d)"
          % (no, os.path.relpath(p, ROOT), wid, hgt, L, T))
    return None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit(__doc__)
    errs = [e for e in (sheet(n) for n in args) if e]
    for e in errs:
        print("★" + e)
    print("完了: %d件中 %d件失敗" % (len(args), len(errs)))
