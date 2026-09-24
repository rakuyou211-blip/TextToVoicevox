# -*- coding: utf-8 -*-
"""英語まじりの画像の読み取り（2026-09-15、RapidOCR同梱後）の確認。
 (1) 判定: 英文の多い画像だけを拾い、英単語だらけの日本語は拾わない
 (2) このPC（WindowsのOCRは日本語のみ・RapidOCRあり）: 英語の画像が英語で読める／
     警告は出ない／日本語の画像は読み直さない
 (3) ocr_win.ps1 が「使った言語・使える言語」を報告する
 (4) 読み手の選び方: Windowsの英語OCR優先 → RapidOCR → どちらも無ければ案内。
     良くならなければ日本語の結果のまま。RapidOCRが壊れていても崩れた結果を黙って返さない
 (5) RapidOCRの行の組み立て（横に割れた断片をつなぐ・重なった書きかけ単語を捨てる）
 (6) クリップボードOCRでも案内が出る
設定と本文は退避して必ず戻す。終了は destroy() だけ。"""
import glob
import os
import shutil
import tempfile

# 私物の絶対パス（本人のPCのユーザー名まで見えてしまう）は、公開のときに困るので外した。
# この台本は tests/_legacy/ にあるので、3つ上がアプリのフォルダ。
APP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 私物のパスは配布・公開のときに困るので外した。ここに入っていたのは本人の
# スクリーンショット（見張りが止まっていると英語で告げるダイアログの実物）。
# 移植先の ../test_ocr_english.py では、_seed.py が同じ文面の画像を作って使っている。
CANARY = "<ここに本人のスクリーンショット（英語のダイアログの実物）のパスが入っていた>"
keep = {}
for name in ("settings.json", "last_text.txt"):
    p = os.path.join(APP, name)
    if os.path.exists(p):
        keep[p] = p + ".enocrbak"
        shutil.copy2(p, keep[p])

ok = []


def check(name, cond):
    if cond:
        ok.append(name)
        print("  OK  " + name)
    else:
        raise AssertionError("NG: " + name)


def kanji_garbage(t):
    """日本語OCRが英文を化かしたときに出る漢字の数（英文なら0に近いはず）"""
    return sum(1 for c in t if "\u4e00" <= c <= "\u9fff")


work = None
try:
    import core
    import main
    from PIL import Image, ImageDraw, ImageFont

    work = tempfile.mkdtemp(prefix="t2v_test_")
    font = ImageFont.truetype(r"C:\Windows\Fonts\meiryo.ttc", 34)

    def render(lines, name):
        im = Image.new("RGB", (1400, 80 + 60 * len(lines)), "white")
        d = ImageDraw.Draw(im)
        for i, ln in enumerate(lines):
            d.text((30, 30 + 60 * i), ln, fill="black", font=font)
        p = os.path.join(work, name)
        im.save(p)
        return p

    JP = render(["吾輩は猫である。名前はまだ無い。",
                 "どこで生れたかとんと見当がつかぬ。"], "jp.png")
    MIX = render(["iPhoneの設定からWi-Fiをオンにしてください。",
                  "Windows 11ではSettingsアプリで言語を追加できます。"], "mix.png")
    TECH = render(["GitHubでPull Requestを作り、CIのtestがpassしたらmergeします。",
                   "READMEにinstall手順とlicenseを書きます。",
                   "PythonのrequestsでJSONをGETします。"], "tech.png")
    EN = render(["Your decoy files are NOT being watched right now.",
                 "Restart it with the launcher script.",
                 "Check the log files for details."], "en.png")
    UI = sorted(glob.glob(os.path.join(APP, "docs", "*.png")))[0]

    # ================= (1) 判定 =================
    print("=== (1) 英文の多い画像だけを拾う ===")
    real_second = core._english_second_pass
    core._english_second_pass = lambda *a, **k: None   # 判定には日本語OCRの生の結果を使う
    try:
        raw = core.run_ocr([JP, MIX, TECH, EN, UI, CANARY], lang="ja",
                           strip_labels=False)
    finally:
        core._english_second_pass = real_second
    for label, p, want in (("日本語だけ", JP, False),
                           ("英単語まじりの日本語", MIX, False),
                           ("技術文書（英語多め）", TECH, False),
                           ("日本語のアプリ画面", UI, False),
                           ("英語だけ", EN, True),
                           ("英語のダイアログ（実物）", CANARY, True)):
        got = core.ocr_looks_english(raw.get(p, ""))
        check("%s → %s" % (label, "英語" if want else "日本語扱い"), got is want)
    check("短すぎる断片は判定しない", core.ocr_looks_english("OK Cancel") is False)

    # ================= (2) このPCで本物の経路 =================
    print("\n=== (2) このPC（日本語OCRのみ＋RapidOCR）で本当に読めるか ===")
    check("RapidOCR が入っている", core.rapidocr_available())
    reread = []
    real_rapid = core.run_rapidocr

    def spy(paths, strip_labels=True, **kw):
        reread.extend(os.path.basename(p) for p in paths)
        return real_rapid(paths, strip_labels=strip_labels, **kw)
    core.run_rapidocr = spy
    try:
        text, w = core.extract_files([CANARY], pdf_mode="auto", dpi=300,
                                     preprocess=True, fix_confusables=True,
                                     denoise=True)
        print("     読めた先頭:", " / ".join(text.strip().splitlines()[:3]))
        # extract_files は前処理した一時コピー（img_0.png など）をOCRに渡すので、
        # 元のファイル名ではなく「1枚だけ読み直した」ことを見る
        check("英語の画像を英語で読み直した（%s）" % reread, len(reread) == 1)
        low = text.lower()
        check("本文の英文が読める（decoy monitor did not start）",
              "decoy monitor did" in low and "start" in low)
        check("「files are」「being watched」も読める",
              "files are" in low and "being watched" in low)
        check("漢字に化けていない（%d字）" % kanji_garbage(text),
              kanji_garbage(text) <= 2)
        check("入っているので「部品が無い」とは言わない",
              core.OCR_ENGLISH_MISSING_MSG not in w)

        for label, path in (("日本語のアプリ画面", UI), ("技術文書", TECH)):
            reread.clear()
            _t, w2 = core.extract_files([path], pdf_mode="auto", dpi=300,
                                        preprocess=True)
            check("%sは読み直さない（RapidOCRを呼ばない）" % label, not reread)
            check("%sでは案内も出ない" % label,
                  core.OCR_ENGLISH_MISSING_MSG not in w2)
    finally:
        core.run_rapidocr = real_rapid

    # ================= (3) ps1 の報告 =================
    print("\n=== (3) ocr_win.ps1 が使った言語を報告する ===")
    meta = {}
    core._run_windows_ocr_chunk([JP], lang="en-US", meta=meta)
    print("     報告:", meta)
    check("英語を頼んでも実際に使ったのは日本語、と分かる",
          meta.get("engine_lang", "").lower().startswith("ja"))
    check("使える言語の一覧が届く", meta.get("available_langs") == ["ja"])

    # ================= (4) 読み手の選び方（模擬）=================
    print("\n=== (4) 読み手の選び方（模擬）===")
    GARBLED = "物し「decoy創es are NO丁匯i叩watched「ight now. Resta代 it with 「i pt.exe"
    CLEAN = "Your decoy files are NOT being watched right now. Restart it with wscript.exe"
    NIHONGO = "吾輩は猫である。名前はまだ無い。どこで生れたかとんと見当がつかぬ。"
    calls, rapid_calls = [], []
    saved = (core._run_windows_ocr_chunk, core.rapidocr_available,
             core.run_rapidocr, core._rapidocr_engine)

    def ja_then(avail):
        def fake(paths, lang="ja", strip_labels=True, errors=None, meta=None):
            calls.append((tuple(paths), lang))
            if meta is not None and avail is not None:
                meta["available_langs"] = list(avail)
            if lang == "ja":
                return {p: (GARBLED if "en" in os.path.basename(p) else NIHONGO)
                        for p in paths}
            return {p: CLEAN for p in paths}
        return fake

    def fake_rapid(paths, strip_labels=True, **kw):
        rapid_calls.append(tuple(paths))
        return {p: CLEAN for p in paths}

    try:
        # a) Windowsに英語OCRがある → そちらを使い、RapidOCRは使わない
        core._run_windows_ocr_chunk = ja_then(["ja", "en-US"])
        core.rapidocr_available = lambda: True
        core.run_rapidocr = fake_rapid
        core._rapidocr_engine = lambda: object()
        notes = []
        res = core.run_windows_ocr(["a_en.png", "b_jp.png"], notices=notes)
        check("Windowsに英語があればそれで読み直す", res["a_en.png"] == CLEAN
              and [c for c in calls if c[1] != "ja"] == [(("a_en.png",), "en-US")])
        check("そのときRapidOCRは使わない（重いものを読み込まない）", not rapid_calls)
        check("日本語の画像はそのまま", res["b_jp.png"] == NIHONGO)

        # b) Windowsは日本語だけ → RapidOCR
        calls.clear()
        core._run_windows_ocr_chunk = ja_then(["ja"])
        notes = []
        res = core.run_windows_ocr(["a_en.png", "b_jp.png"], notices=notes)
        check("Windowsが日本語だけならRapidOCRで読み直す",
              res["a_en.png"] == CLEAN and rapid_calls == [("a_en.png",)])
        check("日本語の画像はRapidOCRに回さない", res["b_jp.png"] == NIHONGO)
        check("警告は出さず、英語で読んだことを記録",
              "english_ocr_missing" not in notes and "english_ocr_used" in notes)

        # c) 良くならなければ日本語の結果のまま
        core.run_rapidocr = lambda paths, strip_labels=True, **kw: {p: "" for p in paths}
        res = core.run_windows_ocr(["a_en.png"], notices=[])
        check("読み直しても読めなければ元の結果を捨てない", res["a_en.png"] == GARBLED)

        # d) RapidOCRが入っていない → 案内
        core.rapidocr_available = lambda: False
        notes = []
        core.run_windows_ocr(["a_en.png"], notices=notes)
        check("どちらも無ければ案内する", "english_ocr_missing" in notes)

        # e) 入っているのに読み込めない（DLL不足など）→ 黙って崩れた結果を返さない
        core.rapidocr_available = lambda: True

        def broken():
            raise ImportError("DLL load failed")
        core._rapidocr_engine = broken
        notes = []
        core.run_windows_ocr(["a_en.png"], notices=notes)
        check("RapidOCRが壊れていたら案内に回す", "english_ocr_missing" in notes)

        # f) 言語の報告が無い古い ps1・notices無しの呼び出し
        core._run_windows_ocr_chunk = ja_then(None)
        core.rapidocr_available = lambda: False
        notes = []
        core.run_windows_ocr(["a_en.png"], notices=notes)
        check("古い ps1 でも安全側（案内する）", "english_ocr_missing" in notes)
        check("notices を渡さない呼び出し（cli など）でも落ちない",
              core.run_windows_ocr(["a_en.png"]) is not None)
    finally:
        (core._run_windows_ocr_chunk, core.rapidocr_available,
         core.run_rapidocr, core._rapidocr_engine) = saved

    # ================= (5) 行の組み立て =================
    print("\n=== (5) RapidOCRの行の組み立て ===")

    def box(x0, x1, y0, y1):
        return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    res = [
        [box(300, 520, 20, 60), "files are", 0.9],     # 同じ行の右側（先に来ても並べ直す）
        [box(10, 330, 22, 58), "Your decoy f", 0.98],  # 右端が次の断片に重なっている
        [box(10, 400, 90, 130), "Restart it with:", 0.99],
    ]
    lines = core._rapidocr_lines(res, 1000, 200)
    got = [l["text"] for l in lines]
    print("     組み立て:", got)
    check("横に割れた断片を1行につなぐ", len(got) == 2)
    check("重なった書きかけの単語（f）を捨てる", got[0] == "Your decoy files are")
    check("上から順に並ぶ", got[1] == "Restart it with:")
    check("座標は0〜1に正規化されている",
          all(0 <= l[k] <= 1 for l in lines for k in ("x0", "x1", "y0", "y1")))
    check("重なっていない別の単語は捨てない",
          [l["text"] for l in core._rapidocr_lines(
              [[box(10, 100, 0, 40), "Check", 1.0],
               [box(120, 300, 0, 40), "Checklist", 1.0]], 1000, 100)]
          == ["Check Checklist"])

    # ================= (5b) 英語の結果を採る決め方 =================
    print()
    print("=== (5b) 英語で読み直した結果を採る決め方 ===")
    better = core._english_ocr_better
    g44 = "物し「decoy創es are NO丁匯i叩watched「ight now. Resta代 it with 「i pt.exe"
    c40 = "Your decoy files are NOT being watched right now."
    check("化けた結果（英字44）より、少し欠けたきれいな英文（英字40）を採る", better(g44, c40))
    check("何も読めなかったら採らない", not better(g44, ""))
    check("化けの印が増えるなら採らない", not better(g44, c40 + " 漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字"))
    check("英字が7割未満しか残らないなら採らない", not better(g44, "Your decoy"))
    check("日本語OCRが化けていなければ、英字の多い方", not better(c40, "Your decoy files"))

    # ================= (6) クリップボードOCR =================
    print("\n=== (6) クリップボードOCRでも伝える ===")
    app = main.App()
    app.withdraw()
    shown = []
    real_warn = main.messagebox.showwarning
    main.messagebox.showwarning = lambda title, msg: shown.append(msg)
    try:
        app._dispatch_msg(("clip_done", "テスト", {}, [core.OCR_ENGLISH_MISSING_MSG]))
        # そっけない警告ではなく、直し方まで出す案内の窓に回す（2026-09-16から）
        check("部品が使えないときは案内の窓を開く",
              app._en_help_win.winfo_exists() and not shown)
        app._en_help_win.destroy()
        shown.clear()
        app._dispatch_msg(("clip_done", "テスト", {}, []))
        check("何もなければ、警告も案内も出さない",
              not shown and not app._en_help_win.winfo_exists())
        msg = core.OCR_ENGLISH_MISSING_MSG
        check("案内に「英語OCRを入れる.bat」の道が書いてある",
              "英語OCRを入れる.bat" in msg)
        check("案内にWindowsへ英語を足す道も書いてある",
              "英語（米国）" in msg and "アプリは大きくなりません" in msg)
        check("setup.bat を実行させる古い案内は残っていない",
              "setup.bat" not in msg)
    finally:
        main.messagebox.showwarning = real_warn
        app._stop_ticks()
        app.destroy()

    print("\n*** 英語まじりの読み取り %d項目すべて通過 ***" % len(ok))
finally:
    if work:
        shutil.rmtree(work, ignore_errors=True)   # 途中で落ちても %TEMP% に残さない
    for orig, bak in keep.items():
        shutil.copy2(bak, orig)
        os.remove(bak)
    print("設定と本文を戻した")
