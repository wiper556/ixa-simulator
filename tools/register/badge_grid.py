import os as _os
import sys as _sys
# リポジトリの根はこのファイルの位置から求める(決め打ちにするとCIやworktreeで壊れる)
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
HERE = _os.path.join(ROOT, "tools", "register", "_work")
_os.makedirs(HERE, exist_ok=True)
_sys.path.insert(0, _os.path.join(ROOT, "tools", "register"))
# -*- coding: utf-8 -*-
"""複数の武将の統率バッジを1枚に並べる(D-01の目視確認を一度にやるため)。
並びは カードの 上段左=槍 上段右=馬 / 下段左=弓 下段右=器。"""
import os
import sys

from PIL import Image, ImageDraw

sys.stdout.reconfigure(encoding="utf-8")
SRC = _os.path.join(ROOT, "assets", "img", "characters", "no%s_full.png")
OUT = _os.path.join(HERE, "badges.png")

BOX = (128, 152, 232, 218)   # バッジ4つが入る範囲
SCALE = 5
LABEL = 26

nos = sys.argv[1:]
tiles = []
for n in nos:
    p = SRC % n
    if not os.path.exists(p):
        print("★ 画像なし %s" % n)
        continue
    im = Image.open(p).crop(BOX)
    im = im.resize((im.width * SCALE, im.height * SCALE), Image.LANCZOS)
    tiles.append((n, im))

if not tiles:
    sys.exit(1)
w, h = tiles[0][1].size
cols = 3
rows = (len(tiles) + cols - 1) // cols
canvas = Image.new("RGB", (cols * w, rows * (h + LABEL)), (255, 255, 255))
d = ImageDraw.Draw(canvas)
for i, (n, im) in enumerate(tiles):
    x, y = (i % cols) * w, (i // cols) * (h + LABEL)
    d.text((x + 6, y + 6), "No.%s   槍/馬 上段  弓/器 下段" % n, fill=(0, 0, 0))
    canvas.paste(im, (x, y + LABEL))
canvas.save(OUT)
print("%d枚を %s に並べた (%dx%d)" % (len(tiles), OUT, canvas.width, canvas.height))
