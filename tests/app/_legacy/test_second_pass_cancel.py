# -*- coding: utf-8 -*-
"""英語の読み直しの「止まる・知らせる・速い」の確認（2026-09-15）。
 (A) run_rapidocr: キャンセルが1枚ごとに効く／進捗が1枚ごとに届く
 (B) _english_second_pass（RapidOCR経路）: 途中で止めたら残りは読まず、読めた分だけ使う。
     進捗は「english」の段として、英語の画像の枚数を分母に届く
 (C) _english_second_pass（Windowsの英語OCR経路）: チャンクごとにキャンセルが効く
 (D) 本物の extract_files: 読み直し中は「英語を読み直しています… i/n枚」と出る
 (E) スレッド数の上限で、本物の読み取りが既定より速い
アプリ本体（main）は読み込まない＝設定に触らない。作業フォルダは finally で必ず消す。"""
import os
import shutil
import tempfile
import threading
import time

ok = []


def check(name, cond):
    if cond:
        ok.append(name)
        print("  OK  " + name)
    else:
        raise AssertionError("NG: " + name)


work = None
try:
    import core
    from PIL import Image, ImageDraw, ImageFont

    work = tempfile.mkdtemp(prefix="t2v_test_")
    font = ImageFont.truetype(r"C:\Windows\Fonts\meiryo.ttc", 40)

    def english_png(name, line="Your decoy files are NOT being watched right now."):
        im = Image.new("RGB", (1400, 140), "white")
        ImageDraw.Draw(im).text((40, 40), line, fill="black", font=font)
        p = os.path.join(work, name)
        im.save(p)
        return p

    pages = [english_png("p%02d.png" % i) for i in range(10)]
    GARBLED = "物し「decoy創es are NO丁匯i叩watched「ight now. Resta代 it with 「i pt.exe"

    class SlowEngine:
        """1枚0.5秒かかる偽のRapidOCR（本物は1枚1〜3秒）"""
        def __init__(self):
            self.calls = 0

        def __call__(self, path):
            self.calls += 1
            time.sleep(0.5)
            return ([[[[0, 0], [600, 0], [600, 40], [0, 40]],
                      "Your decoy files are NOT being watched right now.", 0.99]], 0.5)

    saved = (core._rapidocr_engine, core.rapidocr_available,
             core._run_windows_ocr_chunk)

    # ================= (A) run_rapidocr =================
    print("=== (A) run_rapidocr のキャンセルと進捗 ===")
    eng = SlowEngine()
    core._rapidocr_engine = lambda: eng
    try:
        prog = []
        cancel = threading.Event()
        box = {}
        th = threading.Thread(target=lambda: box.update(out=core.run_rapidocr(
            pages, cancel_event=cancel, progress_cb=lambda d, t: prog.append((d, t)))))
        th.start()
        time.sleep(1.3)                 # 2〜3枚目の最中に押す
        t_cancel = time.perf_counter()
        cancel.set()
        th.join(10)
        stop_lag = time.perf_counter() - t_cancel
        print("     押してから止まるまで %.2f秒 / 読んだ枚数 %d/10" % (stop_lag, eng.calls))
        check("キャンセルが1枚ぶん以内で効く（従来は最大20枚ぶん）", stop_lag < 0.8)
        check("残りは読まない", eng.calls < 10)
        check("読めた分は返す", 0 < len(box["out"]) < 10)
        check("進捗が1枚ごとに届く", [d for d, _t in prog] == list(range(len(prog))))
        check("進捗の分母は全体の枚数", all(t == 10 for _d, t in prog))

        eng2 = SlowEngine()
        core._rapidocr_engine = lambda: eng2
        prog = []
        out = core.run_rapidocr(pages[:3], progress_cb=lambda d, t: prog.append((d, t)))
        check("最後まで読んだら「3/3」まで届く", prog[-1] == (3, 3) and len(out) == 3)
    finally:
        core._rapidocr_engine = saved[0]

    # ================= (B) 読み直し（RapidOCR経路） =================
    print("\n=== (B) 英語の読み直し（RapidOCR経路） ===")
    eng3 = SlowEngine()
    core._rapidocr_engine = lambda: eng3
    core.rapidocr_available = lambda: True
    try:
        result = {p: GARBLED for p in pages}
        prog, notes = [], []
        cancel = threading.Event()

        def pcb(*args):
            prog.append(args)
        th = threading.Thread(target=core._english_second_pass, args=(
            result, {"available_langs": ["ja"]}, False, notes, cancel),
            kwargs={"progress_cb": pcb})
        th.start()
        time.sleep(1.8)
        t_cancel = time.perf_counter()
        cancel.set()
        th.join(10)
        stop_lag = time.perf_counter() - t_cancel
        replaced = sum(1 for p in pages if result[p] != GARBLED)
        print("     押してから止まるまで %.2f秒 / 英語にできた %d/10枚" % (stop_lag, replaced))
        check("読み直し中のキャンセルが1枚ぶん以内で効く", stop_lag < 0.8)
        check("読めた分だけ英語にし、残りは元のまま", 0 < replaced < 10)
        check("進捗は「english」の段として届く",
              prog and all(len(a) == 3 and a[2] == "english" for a in prog))
        check("分母は英語の画像の枚数（10）", all(a[1] == 10 for a in prog))
    finally:
        core._rapidocr_engine, core.rapidocr_available = saved[0], saved[1]

    # ================= (C) 読み直し（Windowsの英語OCR経路） =================
    print("\n=== (C) 英語の読み直し（Windowsの英語OCR経路） ===")
    chunk_calls = []

    def slow_chunk(paths, lang="ja", strip_labels=True, errors=None, meta=None):
        chunk_calls.append(len(paths))
        time.sleep(0.6)
        return {p: "Your decoy files are NOT being watched right now." for p in paths}
    core._run_windows_ocr_chunk = slow_chunk
    try:
        result = {p: GARBLED for p in pages}
        cancel = threading.Event()
        prog = []
        th = threading.Thread(target=core._english_second_pass, args=(
            result, {"available_langs": ["ja", "en-US"]}, False, [], cancel),
            kwargs={"chunk_size": 2, "progress_cb": lambda *a: prog.append(a)})
        th.start()
        time.sleep(1.4)
        cancel.set()
        th.join(10)
        print("     チャンク呼び出し %s" % chunk_calls)
        check("チャンクの切れ目でキャンセルが効く", len(chunk_calls) < 5)
        check("こちらも進捗は「english」の段", prog and all(a[2] == "english" for a in prog))
    finally:
        core._run_windows_ocr_chunk = saved[2]

    # ================= (D) 本物の extract_files =================
    print("\n=== (D) 本物の抽出で、読み直し中の文言が出る ===")
    msgs = []
    text, _w = core.extract_files(pages[:3], pdf_mode="auto", dpi=300, preprocess=True,
                                  progress_cb=lambda d, t, m: msgs.append((d, t, m)))
    en_msgs = [m for m in msgs if "英語を読み直しています" in m[2]]
    print("     読み直しの通知:", [m[2] for m in en_msgs])
    check("「英語を読み直しています… i/n枚」が出る", len(en_msgs) >= 2)
    check("分母は英語の画像の枚数（3）", all(m[1] == 3 for m in en_msgs))
    check("最後は 3/3 まで届く", en_msgs[-1][0] == 3)
    check("読み直した結果は英語", "decoy" in text.lower())

    # ================= (E) スレッド数の上限 =================
    print("\n=== (E) スレッド数の上限で速くなっているか ===")
    # 私物のパスは配布・公開のときに困るので外した。ここに入っていたのは本人の
    # スクリーンショット（英語のダイアログの実物）。移植先の ../test_ocr_cancel.py では
    # 種データ（_seed.py）で作った英語の画像に置き換えてある。
    CANARY = "<ここに本人のスクリーンショット（英語のダイアログの実物）のパスが入っていた>"
    core._RAPIDOCR["engine"] = None
    e = core._rapidocr_engine()
    e(CANARY)
    ts = []
    for _ in range(5):
        t = time.perf_counter()
        e(CANARY)
        ts.append(time.perf_counter() - t)
    avg = sum(ts) / len(ts)
    print("     アプリ経由の読み取り平均 %.2f秒（上限前の実測: 既定1.48秒 / 4スレッド0.65秒）" % avg)
    check("既定（1.48秒）より明らかに速い（1.1秒未満）", avg < 1.1)

    print("\n*** 読み直しの停止・進捗・速さ %d項目すべて通過 ***" % len(ok))
finally:
    if work:
        shutil.rmtree(work, ignore_errors=True)
