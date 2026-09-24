# -*- coding: utf-8 -*-
"""英語OCR（RapidOCR）に意地悪な入力を与えて、落ちない・固まらないかを見る。
アプリ本体（main）は読み込まない＝設定に触らない。作業フォルダは finally で必ず消す。"""
import os, shutil, tempfile, threading, time
import core
from PIL import Image, ImageDraw, ImageFont

work = tempfile.mkdtemp(prefix="t2v_test_")
problems = []
try:
    font = ImageFont.truetype(r"C:\Windows\Fonts\meiryo.ttc", 40)
    LINE = "Your decoy files are NOT being watched right now."

    def make(name, mode, size, draw_text=True, bg=None):
        im = Image.new(mode, size, bg if bg is not None else ("white" if mode != "RGBA" else (255, 255, 255, 0)))
        if draw_text:
            d = ImageDraw.Draw(im)
            fill = 0 if mode in ("L", "P", "1") else ("black" if mode != "RGBA" else (0, 0, 0, 255))
            d.text((40, 40), LINE, fill=fill, font=font)
        p = os.path.join(work, name)
        im.save(p)
        return p

    cases = []
    corrupt = os.path.join(work, "corrupt.png")
    with open(corrupt, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + os.urandom(3000))
    cases.append(("壊れたPNG", corrupt, "empty_ok"))
    cases.append(("存在しないファイル", os.path.join(work, "nothing.png"), "empty_ok"))
    cases.append(("1×1ピクセル", make("tiny.png", "RGB", (1, 1), draw_text=False), "empty_ok"))
    cases.append(("真っ白（文字なし）", make("blank.png", "RGB", (1200, 400), draw_text=False), "empty_ok"))
    cases.append(("透過PNG（RGBA）", make("rgba.png", "RGBA", (1400, 140)), "text"))
    cases.append(("グレースケール（L）", make("gray.png", "L", (1400, 140), bg=255), "text"))
    pal = Image.open(make("rgb_for_p.png", "RGB", (1400, 140))).convert("P")
    ppath = os.path.join(work, "palette.png"); pal.save(ppath)
    cases.append(("パレット（P）", ppath, "text"))
    huge = Image.new("RGB", (9000, 6000), "white")
    ImageDraw.Draw(huge).text((200, 200), LINE, fill="black", font=ImageFont.truetype(r"C:\Windows\Fonts\meiryo.ttc", 160))
    hpath = os.path.join(work, "huge.png"); huge.save(hpath); del huge
    cases.append(("巨大画像 9000×6000", hpath, "text"))

    core._rapidocr_engine()   # 読み込み時間を各ケースに混ぜない
    for label, path, expect in cases:
        t = time.perf_counter()
        try:
            out = core.run_rapidocr([path])
            err = None
        except Exception as e:
            out, err = {}, "%s: %s" % (type(e).__name__, e)
        dt = time.perf_counter() - t
        txt = out.get(path)
        if err:
            verdict = "NG（例外で落ちた）"; problems.append((label, err))
        elif expect == "empty_ok":
            verdict = "OK（落ちずに空/無し）" if not (txt or "").strip() else "OK（何か読めた）"
        else:
            good = txt is not None and "decoy" in txt.lower()
            verdict = "OK（読めた）" if good else "要確認（読めない）"
            if not good:
                problems.append((label, "読めなかった: %r" % txt))
        if dt > 30:
            problems.append((label, "時間がかかりすぎ %.1f秒" % dt))
        print("%-20s %6.2f秒  %s  %s" % (label, dt, verdict, (err or (txt or "").replace("\n", " / "))[:60]))

    t = time.perf_counter()
    r = core.run_rapidocr([])
    print("%-20s %6.2f秒  %s" % ("空のリスト", time.perf_counter() - t, "OK" if r == {} else "NG %r" % r))

    # 2つのスレッドから同時に（アプリは busy で同時に走らせないが、壊れないかは見ておく）
    rgba = cases[4][1]
    res, errs = [], []
    def go():
        try:
            res.append(core.run_rapidocr([rgba]).get(rgba, ""))
        except Exception as e:
            errs.append(repr(e))
    ths = [threading.Thread(target=go) for _ in range(3)]
    t = time.perf_counter()
    for th in ths: th.start()
    for th in ths: th.join(120)
    ok = not errs and len(res) == 3 and all("decoy" in x.lower() for x in res)
    print("%-20s %6.2f秒  %s" % ("3スレッド同時", time.perf_counter() - t, "OK" if ok else "NG %r %r" % (errs, res)))
    if not ok:
        problems.append(("3スレッド同時", "%r %r" % (errs, res)))
finally:
    shutil.rmtree(work, ignore_errors=True)
print("\n問題:", problems or "なし")
