# -*- coding: utf-8 -*-
"""撮った画面（capture.py の出力）から、説明書に載せる画像（docs/manual/img/*.jpg）を作る。

    python tools/manual/make_images.py <撮った画面のフォルダ> [出力フォルダ]

切り抜く範囲（JOBS）は、拡大率 250% の画面で撮ったときの画素で決めてある。ほかの拡大率で
撮ったときは、その比で伸び縮みさせる（boxes.json の _scale）。アプリの見た目が変わって
ずれたら、JOBS を直す。できた画像は、必ず目で確かめる。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(ROOT, "docs", "manual", "img")

BASE_SCALE = 2.5   # JOBS と EDGE を決めたときの拡大率
# 窓の撮り方の都合で、左右と下に 16px、上に 2px の黒い縁が付く（窓の影の分）。いつも削る
EDGE = (16, 2, 16, 16)

# (出力名, 元の画面, 切り抜く範囲（外枠ごとの画面の画素） or None（縁だけ削る）, 横幅の上限)
JOBS = [
    ("main", "02_text.png", None, 1400),
    ("empty", "01_empty.png", None, 1100),
    ("step1", "02_text.png", (20, 225, 870, 790), 700),
    ("step2", "02_text.png", (20, 745, 870, 1120), 700),
    ("step3", "02_text.png", (20, 1115, 870, 1545), 700),
    ("text", "02_text.png", (870, 225, 2510, 1170), 1200),
    ("player", "02_text.png", (870, 1660, 2510, 1920), 1200),
    ("opt", "03_opt.png", (20, 225, 870, 1640), 700),
    ("voice", "04_voice.png", (20, 745, 870, 1420), 700),
    ("finish", "05_finish.png", (20, 1185, 870, 1920), 700),
    ("conn", "06_conn.png", (20, 1300, 870, 1920), 700),
    ("replace", "07_replace.png", (870, 225, 2510, 900), 1200),
    ("dict", "08_dict.png", None, 1100),
    ("cache", "09_cache.png", None, 900),
    ("playing", "10_playing.png", (870, 225, 2510, 1920), 1200),
    ("reading", "11_reading.png", None, 1200),
    ("theme_light", "02_text.png", None, 600),
    ("theme_dark", "12_theme_dark.png", None, 600),
    ("theme_zunda", "12_theme_zunda.png", None, 600),
    ("theme_hc", "12_theme_hc.png", None, 600),
]
# 「画面の見かた」の図（02_text.png に、各部分の枠と A〜I の印を描く）
SCREEN_MAP = "screen_map"


def names():
    """作る画像の名前（.jpg）。説明書が使う画像は、すべてここにある（tests/test_manual.py）。"""
    return [name + ".jpg" for name, _src, _box, _w in JOBS] + [SCREEN_MAP + ".jpg"]


def _scaled(values, k):
    return tuple(round(v * k) for v in values)


def _trim(im, k):
    left, top, right, bottom = _scaled(EDGE, k)
    return im.crop((left, top, im.width - right, im.height - bottom))


def _save(im, out_dir, name, maxw):
    from PIL import Image
    if im.width > maxw:
        im = im.resize((maxw, round(im.height * maxw / im.width)), Image.LANCZOS)
    path = os.path.join(out_dir, name + ".jpg")
    im.save(path, quality=84, optimize=True, progressive=True)
    print(name, im.size, os.path.getsize(path) // 1024, "KB")
    return os.path.getsize(path)


def screen_map(shot, boxes, k):
    """「画面の見かた」の図: 各部分を枠で囲み、A〜I の印を付ける（説明書の一覧と同じ並び）。"""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.open(shot).convert("RGB")
    d = ImageDraw.Draw(im, "RGBA")
    accent = (194, 65, 12)

    def px(v):
        return round(v * k)

    s = boxes["steps"]
    x0, x1 = s[0] + px(8), s[2] - px(8)
    regions = [
        boxes["status"],                                                              # A お知らせ
        [boxes["theme"][0], boxes["theme"][1], boxes["links"][2], boxes["links"][3]],  # B テーマ・使い方
        [x0, boxes["step1"][1] - px(14), x1, boxes["step2"][1] - px(22)],             # C ①
        [x0, boxes["step2"][1] - px(14), x1, boxes["step3"][1] - px(22)],             # D ②
        [x0, boxes["step3"][1] - px(14), x1, boxes["step3"][1] + px(470)],            # E ③
        boxes["conn"],                                                                # F 接続
        boxes["rbar"],                                                                # G 本文の道具
        [boxes["text"][0], boxes["text"][1] + px(4), boxes["text"][2],
         boxes["player"][1] - px(70)],                                                # H 本文
        boxes["player"],                                                              # I 再生バー
    ]
    font_path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "YuGothB.ttc")
    font = ImageFont.truetype(font_path, px(46))
    r = px(34)
    for n, (a, b, c, e) in enumerate(regions):
        d.rounded_rectangle((a - px(6), b - px(6), c + px(6), e + px(6)), px(14),
                            outline=accent + (255,), width=px(8), fill=accent + (14,))
        cx, cy = a + r - px(4), b + r - px(4)
        if n in (0, 1):   # 上の帯は狭いので、印は枠の左の外に出す
            cx, cy = a - r - px(14), (b + e) // 2
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=accent + (255,),
                  outline=(255, 255, 255, 255), width=px(5))
        t = "ABCDEFGHI"[n]   # ① ② ③ と見分けがつくよう、数字ではなく文字にする
        tw = d.textlength(t, font=font)
        d.text((cx - tw / 2, cy - px(30)), t, font=font, fill=(255, 255, 255, 255))
    return _trim(im, k)


def main(shots, out_dir=OUT):
    from PIL import Image
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(shots, "boxes.json"), encoding="utf-8") as f:
        boxes = json.load(f)
    k = boxes.get("_scale", BASE_SCALE) / BASE_SCALE
    total = 0
    for name, src, box, maxw in JOBS:
        im = Image.open(os.path.join(shots, src)).convert("RGB")
        im = im.crop(_scaled(box, k)) if box else _trim(im, k)
        total += _save(im, out_dir, name, maxw)
    total += _save(screen_map(os.path.join(shots, "02_text.png"), boxes, k), out_dir, SCREEN_MAP, 1400)
    print("合計", total // 1024, "KB")


if __name__ == "__main__":
    main(sys.argv[1], *sys.argv[2:3])
