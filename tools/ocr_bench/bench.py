# -*- coding: utf-8 -*-
"""画面の文字のOCRを、前処理を変えて読み比べるベンチマーク（Windows / macOS）。

tests/fixtures/screen_ocr/ の画像（make_fixtures.py で作った正解つき）を、
このOSの本物のOCR（Windows.Media.Ocr / Apple Vision）で読んで、正解率を表にする。
    python tools/ocr_bench/bench.py            # 表を標準出力へ
CIでは GITHUB_STEP_SUMMARY にも同じ表を書く。
正解率 = 1 - 編集距離 / 正解の文字数（空白・改行は無視、全角半角はNFKCでそろえる）。
"""
import json
import os
import sys
import tempfile
import time
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import core  # noqa: E402

from PIL import Image, ImageOps  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "screen_ocr")


def norm(s):
    return "".join(ch for ch in unicodedata.normalize("NFKC", s) if not ch.isspace())


def lev(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def accuracy(truth, got):
    t, g = norm(truth), norm(got)
    return max(0.0, 1.0 - lev(t, g) / max(1, len(t)))


# ---------------- 前処理の候補 ----------------
def v_current(img):
    return core.preprocess_image(img, enable=True)


def _light_text(img):
    """背景（いちばん多い明るさの帯）より明るい画素が多ければ「明るい文字」とみなす。"""
    g = img.convert("L")
    hist = g.histogram()
    # 16段に丸めた帯のピーク＝背景
    bands = [sum(hist[i:i + 16]) for i in range(0, 256, 16)]
    bg = bands.index(max(bands)) * 16 + 8
    dark = sum(hist[:max(0, bg - 48)])
    light = sum(hist[min(256, bg + 48):])
    return light > dark


def v_invert_auto(img):
    img = core.preprocess_image(img, enable=False)
    if _light_text(img):
        img = ImageOps.invert(img.convert("RGB"))
    return core.preprocess_image(img, enable=True)


def v_inverted(img):
    img = core.preprocess_image(img, enable=False)
    return core.preprocess_image(ImageOps.invert(img.convert("RGB")), enable=True)


def _upscale(img, k):
    w, h = img.size
    lim = core.WIN_OCR_MAX_DIM if core.IS_WIN else 4000
    k = min(k, lim / max(w, h))
    if k <= 1:
        return img
    return img.resize((int(w * k), int(h * k)), Image.LANCZOS)


def v_x3(img):
    img = core.preprocess_image(img, enable=False)
    return core.preprocess_image(_upscale(img, 3), enable=True)


def v_invert_x3(img):
    img = core.preprocess_image(img, enable=False)
    if _light_text(img):
        img = ImageOps.invert(img.convert("RGB"))
    return core.preprocess_image(_upscale(img, 3), enable=True)


def v_inv_x3(img):
    img = core.preprocess_image(img, enable=False)
    img = ImageOps.invert(img.convert("RGB"))
    return core.preprocess_image(_upscale(img, 3), enable=True)


def v_raw(img):
    return core.preprocess_image(img, enable=False)


def v_otsu_x3(img):
    """グレースケール→(明るい文字なら反転)→3倍→大津の二値化。"""
    img = core.preprocess_image(img, enable=False)
    g = img.convert("L")
    if _light_text(g):
        g = ImageOps.invert(g)
    g = _upscale(g, 3)
    hist = g.histogram()
    total = sum(hist)
    sum_all = sum(i * h for i, h in enumerate(hist))
    wb = sb = 0
    best, th = -1, 128
    for t in range(256):
        wb += hist[t]
        if wb == 0:
            continue
        wf = total - wb
        if wf == 0:
            break
        sb += t * hist[t]
        mb, mf = sb / wb, (sum_all - sb) / wf
        between = wb * wf * (mb - mf) ** 2
        if between > best:
            best, th = between, t
    return g.point(lambda v: 255 if v > th else 0)


VARIANTS = [
    ("raw", v_raw),
    ("current", v_current),
    ("inverted", v_inverted),
    ("invert_auto", v_invert_auto),
    ("x3", v_x3),
    ("invert_x3", v_invert_x3),
    ("inv_x3", v_inv_x3),
    ("otsu_x3", v_otsu_x3),
]


def ocr_all(paths, strip_labels=False):
    notices = []
    t0 = time.time()
    res = core.run_ocr(paths, strip_labels=strip_labels, notices=notices)
    return res, time.time() - t0, notices


def main():
    with open(os.path.join(FIX, "truth.json"), encoding="utf-8") as fp:
        cases = json.load(fp)
    names = list(cases)
    table = {}      # (case, variant) -> (acc, text)
    timing = {}
    with tempfile.TemporaryDirectory(prefix="t2v_bench_") as tmp:
        for vname, fn in VARIANTS:
            paths = []
            for c in names:
                img = Image.open(os.path.join(FIX, c + ".png"))
                p = os.path.join(tmp, f"{vname}__{c}.png")
                fn(img).save(p)
                paths.append(p)
            res, sec, notices = ocr_all(paths)
            timing[vname] = sec
            for c, p in zip(names, paths):
                got = res.get(p, "")
                table[(c, vname)] = (accuracy(cases[c]["truth"], got), got)
            if notices:
                print(f"[{vname}] notices: {notices}")
        # いまのアプリの流れそのもの（ラベル除去・ノイズ除去・誤字補正まで）
        paths = []
        for c in names:
            p = os.path.join(tmp, f"app__{c}.png")
            v_current(Image.open(os.path.join(FIX, c + ".png"))).save(p)
            paths.append(p)
        res, sec, _ = ocr_all(paths, strip_labels=True)
        timing["app_now"] = sec
        for c, p in zip(names, paths):
            got = core.denoise_capture(core.fix_ocr_confusables(res.get(p, "")))
            table[(c, "app_now")] = (accuracy(cases[c]["truth"], got), got)
        # 「📷 画面から読む」の流れ（囲んだ範囲なのでラベル・ノイズ除去はしない）
        res, sec, _ = ocr_all(paths, strip_labels=False)
        timing["screen_app"] = sec
        for c, p in zip(names, paths):
            got = core.fix_ocr_confusables(res.get(p, ""))
            table[(c, "screen_app")] = (accuracy(cases[c]["truth"], got), got)
        # 普通 と 反転 を両方読んで、日本語らしさのスコアが高いほうを採る（正解は見ない）
        for c in names:
            table[(c, "best_of_2")] = max(
                [table[(c, "x3")], table[(c, "inv_x3")]],
                key=lambda r: core._ocr_text_score(r[1]))

    vnames = ["app_now", "screen_app"] + [v for v, _ in VARIANTS] + ["best_of_2"]
    plat = "Windows" if core.IS_WIN else ("macOS" if core.IS_MAC else sys.platform)
    lines = [f"### 画面の文字のOCR 正解率（{plat}）", "",
             "| 画像 | " + " | ".join(vnames) + " |",
             "|---|" + "---|" * len(vnames)]
    for c in names:
        row = [f"{c}"]
        best = max(table[(c, v)][0] for v in vnames)
        for v in vnames:
            a = table[(c, v)][0]
            cell = f"{a * 100:.0f}"
            row.append(f"**{cell}**" if a == best else cell)
        lines.append("| " + " | ".join(row) + " |")
    avg = ["**平均**"] + [f"{sum(table[(c, v)][0] for c in names) / len(names) * 100:.1f}"
                         for v in vnames]
    lines.append("| " + " | ".join(avg) + " |")
    lines.append("| 秒(9枚) | " + " | ".join(
        f"{timing.get(v, 0):.1f}" if v in timing else "-" for v in vnames) + " |")
    lines += ["", "<details><summary>読み取った文字（app_now / best_of_2 / otsu_x3）</summary>", ""]
    for c in names:
        lines.append(f"**{c}**（正解: `{norm(cases[c]['truth'])}`）")
        for v in ("app_now", "best_of_2", "otsu_x3"):
            lines.append(f"- {v}: `{norm(table[(c, v)][1])}`")
        lines.append("")
    lines.append("</details>")
    out = "\n".join(lines)
    print(out)
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as fp:
            fp.write(out + "\n")


if __name__ == "__main__":
    main()
