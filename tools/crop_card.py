# -*- coding: utf-8 -*-
"""武将カードのスクリーンショットから TYPE1/TYPE2 を切り抜く。

切り抜きの絶対ルール(2026-07-29確定):
  TYPE2(カードのみ)          = 224 x 315
  TYPE1(カード+スキルパネル) = 466 x 315
元スクリーンショットの解像度がバッチごとに違っても出力サイズは必ずこれに揃える
(拡大縮小はしない。カード左下の赤いハートを基準に絶対位置合わせするだけ)。

ハート基準の位置合わせ:
  承認済み画像(224x315)ではハートの左上が (x=6, y=267)。
  検出したハートが同じ位置に来るよう cropLeft = minX - 6 / cropTop = minY - 267 とする。

誤検出対策:
  ・x探索範囲は割合ではなく絶対55px(ダメなら40px)に固定する
  ・候補ピクセルの中央値から15px以上離れた孤立点は除外する
  ・正常なハートは概ね 19x19 / 94px。大きく外れたら誤検出として再試行する

使い方:
  python tools/crop_card.py <No> [<No> ...]      # 元スクリーンショット/スクリーンショット_<No>.png を処理
  python tools/crop_card.py --all                # アーカイブ内の全No.を処理(既存ファイルは上書きしない)
"""
import sys, pathlib
from PIL import Image

ARCH = pathlib.Path(r"C:\Users\uesug\ixa-simulator-char-screenshots\元スクリーンショット")
CROPTEST = pathlib.Path(r"C:\Users\uesug\ixa-simulator-char-screenshots\crop_test")
DEST = pathlib.Path(__file__).resolve().parent.parent / "assets" / "img" / "characters"

HEART = (206, 18, 38)
TOL = 20
ANCHOR_X, ANCHOR_Y = 6, 267
TYPE2 = (224, 315)
TYPE1 = (466, 315)


def find_heart(im, xlimit):
    """ハート(概ね19x19・約94px)の左上を返す。

    2026-08-16: **中央値で絞る方法は特カードで通用しない。** 特の枠は赤で、
    ハートと同じ色域に入る。枠は縦に長いので候補の中央値がそちらへ引っ張られ、
    7x31 という縦棒が「ハート」として返っていた(今日の119枚が全滅)。
    赤い塊を連結成分に分け、**形がハートに一番近い塊**を選ぶ。
    """
    px = im.convert("RGB").load()
    W, H = im.size
    red = set()
    for y in range(int(H * 0.55), H):
        for x in range(0, min(xlimit, W)):
            r, g, b = px[x, y]
            if abs(r - HEART[0]) <= TOL and abs(g - HEART[1]) <= TOL and abs(b - HEART[2]) <= TOL:
                red.add((x, y))
    if not red:
        return None, "ハートが見つからない"

    # 連結成分に分ける(8近傍)
    best = None
    seen = set()
    for s in red:
        if s in seen:
            continue
        stack, comp = [s], []
        seen.add(s)
        while stack:
            cx, cy = stack.pop()
            comp.append((cx, cy))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (cx + dx, cy + dy)
                    if n in red and n not in seen:
                        seen.add(n)
                        stack.append(n)
        minX = min(p[0] for p in comp); minY = min(p[1] for p in comp)
        w = max(p[0] for p in comp) - minX + 1
        h = max(p[1] for p in comp) - minY + 1
        # ハートらしさ: 19x19・94px からの離れ具合。縦棒(w<<h)は大きく外れる
        score = abs(w - 19) + abs(h - 19) + abs(len(comp) - 94) / 10.0
        if best is None or score < best[0]:
            best = (score, minX, minY, w, h, len(comp))
    if best is None:
        return None, "赤い塊が1つも無い"
    return (best[1], best[2], best[3], best[4], best[5]), None


def crop_one(no, verbose=True):
    src = ARCH / ("スクリーンショット_%s.png" % no)
    if not src.exists():
        return "元スクリーンショットが無い: %s" % src.name
    im = Image.open(src)
    res = err = None
    for xlimit in (55, 40):
        res, err = find_heart(im, xlimit)
        if res and not (res[2] > 25 or res[3] > 25 or res[4] > 110):
            break
    if not res:
        return "No.%s ハート検出失敗(%s)" % (no, err)
    minX, minY, hw, hh, n = res
    if hw > 25 or hh > 25 or n > 110:
        return "No.%s ハート検出が不正 (%dx%d, %dpx)" % (no, hw, hh, n)
    left, top = minX - ANCHOR_X, minY - ANCHOR_Y
    if left < 0 or top < 0:
        return "No.%s 切り抜き基点が負 (left=%d top=%d)" % (no, left, top)
    DEST.mkdir(parents=True, exist_ok=True)
    for label, (w, h) in (("char", TYPE2), ("full", TYPE1)):
        box = (left, top, left + w, top + h)
        if box[2] > im.size[0] or box[3] > im.size[1]:
            return "No.%s 元画像より切り抜き範囲が大きい %s > %s" % (no, box, im.size)
        out = im.crop(box)
        out.save(DEST / ("no%s_%s.png" % (no, label)))
        sub = CROPTEST / ("TYPE2" if label == "char" else "TYPE1")
        sub.mkdir(parents=True, exist_ok=True)
        out.save(sub / ("%s_%s.png" % (no, "type2" if label == "char" else "type1")))
    if verbose:
        print("No.%-6s ハート(%d,%d) %dx%d/%dpx → 基点(%d,%d) 出力OK"
              % (no, minX, minY, hw, hh, n, left, top))
    return None


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--all":
        nos = sorted((p.stem.split("_")[-1] for p in ARCH.glob("*.png")), key=int)
    else:
        nos = args
    errs = [e for e in (crop_one(n) for n in nos) if e]
    for e in errs:
        print("★" + e)
    print("完了: %d件中 %d件失敗" % (len(nos), len(errs)))
