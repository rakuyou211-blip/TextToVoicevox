# -*- coding: utf-8 -*-
"""「📷 画面から読む」で実際に囲まれそうな画面の文字を、正解つきの画像にする。

生成物は tests/fixtures/screen_ocr/ に置いてコミットする（CIのWindows/Macで
同じ画像を読ませて、前処理の良し悪しを数字で比べるため）。作り直すときだけ実行:
    python tools/ocr_bench/make_fixtures.py
フォントは Noto Sans CJK JP（Linuxの fonts-noto-cjk）。Windowsの Yu Gothic UI に近い。
"""
import json
import math
import os
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "tests", "fixtures", "screen_ocr")
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def font(px):
    return ImageFont.truetype(FONT, px, index=0)   # index 0 = JP


def wallpaper(w, h, seed=1):
    """Windows 11 風の青い“花びら”壁紙（なめらかな濃淡と大きな曲線）。"""
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            t = (x / w + y / h) / 2
            s = 0.5 + 0.5 * math.sin(x / 37.0 + y / 53.0)
            r = int(20 + 60 * t + 30 * s)
            g = int(70 + 80 * t + 40 * s)
            b = int(160 + 70 * s)
            px[x, y] = (min(r, 255), min(g, 255), min(b, 255))
    d = ImageDraw.Draw(img)
    rnd = random.Random(seed)
    for _ in range(6):
        cx, cy = rnd.randint(0, w), rnd.randint(0, h)
        rr = rnd.randint(w // 4, w)
        d.ellipse((cx - rr, cy - rr // 2, cx + rr, cy + rr // 2),
                  outline=(120, 190, 255), width=rnd.randint(6, 18))
    return img.filter(ImageFilter.GaussianBlur(6))


def desktop_labels(scale):
    """デスクトップのアイコン名（白い文字＋黒い影・壁紙の上・中央ぞろえで2行に折り返し）。"""
    px = round(12 * scale)
    f = font(px)
    labels = [["WEBテスト_非言語ト", "レーナー.html"],
              ["論文5_プレビュー.", "html"],
              ["BitLocker回復キー_", "重要_PC外に退避"],
              ["スクリーンショット", "2026-09-24"]]
    tmp = ImageDraw.Draw(Image.new("L", (1, 1)))
    w = int(max(tmp.textlength(l, font=f) for ls in labels for l in ls)) + round(16 * scale)
    slot_h = round(110 * scale)
    h = slot_h * len(labels)
    img = wallpaper(w, h)
    shadow = Image.new("L", (w, h), 0)
    sd = ImageDraw.Draw(shadow)
    txt = Image.new("L", (w, h), 0)
    td = ImageDraw.Draw(txt)
    lh = round(px * 1.35)
    for i, lines in enumerate(labels):
        y0 = i * slot_h + round(56 * scale)   # 上の56pxはアイコン画像の場所
        for j, line in enumerate(lines):
            tw = td.textlength(line, font=f)
            x = (w - tw) / 2
            y = y0 + j * lh
            sd.text((x + 1, y + 1), line, font=f, fill=255)
            td.text((x, y), line, font=f, fill=255)
    shadow = shadow.filter(ImageFilter.GaussianBlur(1.2 * scale))
    img = Image.composite(Image.new("RGB", (w, h), (0, 0, 0)), img,
                          shadow.point(lambda v: int(v * 0.8)))
    img = Image.composite(Image.new("RGB", (w, h), (255, 255, 255)), img, txt)
    return img, "\n".join(l for ls in labels for l in ls)


PROSE = ["吾輩は猫である。名前はまだ無い。",
         "どこで生れたかとんと見当がつかぬ。",
         "何でも薄暗いじめじめした所でニャーニャー泣いていた事だけは記憶している。"]


def page(px, fg, bg, lines=PROSE, pad=None, width=None):
    f = font(px)
    pad = pad if pad is not None else px
    lh = round(px * 1.7)
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    w = width or int(max(tmp.textlength(l, font=f) for l in lines) + pad * 2)
    h = lh * len(lines) + pad * 2
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    for i, l in enumerate(lines):
        d.text((pad, pad + i * lh), l, font=f, fill=fg)
    return img, "\n".join(lines)


def button_row(scale):
    """色つきボタンの白い文字（アプリの画面を囲んだとき）。"""
    px = round(12 * scale)
    f = font(px)
    items = [("保存する", (0, 120, 212)), ("キャンセル", (196, 43, 28)),
             ("次のページへ", (16, 124, 16))]
    pad = round(10 * scale)
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    widths = [int(tmp.textlength(t, font=f)) + pad * 2 for t, _ in items]
    w = sum(widths) + pad * (len(items) + 1)
    h = px + pad * 4
    img = Image.new("RGB", (w, h), (243, 243, 243))
    d = ImageDraw.Draw(img)
    x = pad
    for (t, c), bw in zip(items, widths):
        d.rounded_rectangle((x, pad, x + bw, h - pad), radius=round(4 * scale), fill=c)
        d.text((x + pad, pad + (h - 2 * pad - px) / 2 - px * 0.15), t, font=f,
               fill=(255, 255, 255))
        x += bw + pad
    return img, "\n".join(t for t, _ in items)


def main():
    os.makedirs(OUT, exist_ok=True)
    cases = {}

    def add(name, pair, note):
        img, truth = pair
        img.save(os.path.join(OUT, name + ".png"), optimize=True)
        cases[name] = {"truth": truth, "note": note}

    add("desktop_100", desktop_labels(1.0), "デスクトップのアイコン名・表示100%")
    add("desktop_150", desktop_labels(1.5), "デスクトップのアイコン名・表示150%")
    add("ebook_16", page(16, (30, 30, 30), (250, 247, 240)), "電子書籍の本文・16px")
    add("ebook_24", page(24, (30, 30, 30), (250, 247, 240)), "電子書籍の本文・24px")
    add("web_13", page(13, (20, 20, 20), (255, 255, 255)), "Webの小さめの文字・13px")
    add("dark_14", page(14, (212, 212, 212), (30, 30, 30)), "ダークモード・14px")
    add("dark_20", page(20, (230, 230, 230), (32, 33, 36)), "ダークモード・20px")
    add("buttons_100", button_row(1.0), "色つきボタンの白い文字・100%")
    add("buttons_150", button_row(1.5), "色つきボタンの白い文字・150%")
    with open(os.path.join(OUT, "truth.json"), "w", encoding="utf-8") as fp:
        json.dump(cases, fp, ensure_ascii=False, indent=1)
    print(f"{len(cases)} cases -> {OUT}")


if __name__ == "__main__":
    main()
