# -*- coding: utf-8 -*-
"""アプリの中の「英語の画像は？」案内の確認（2026-09-16）。
 - 常設ボタンがある／押すと説明が開く
 - 部品が入っているかで文面とボタンが変わる
 - ボタンは本当に Windows の言語設定と .bat を開こうとする（実際には開かず記録するだけ）
 - 英文が崩れたときの警告が、文章だけで終わらずこの案内に回る
設定と本文は退避して必ず戻す。終了は destroy() だけ。"""
import os
import shutil

# 私物の絶対パス（本人のPCのユーザー名まで見えてしまう）は、公開のときに困るので外した。
# この台本は tests/_legacy/ にあるので、3つ上がアプリのフォルダ。
APP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
keep = {}
for name in ("settings.json", "last_text.txt"):
    p = os.path.join(APP, name)
    if os.path.exists(p):
        keep[p] = p + ".enhelpbak"
        shutil.copy2(p, keep[p])

ok = []


def check(name, cond):
    if cond:
        ok.append(name)
        print("  OK  " + name)
    else:
        raise AssertionError("NG: " + name)


def texts(widget):
    """その窓の中の文字を全部集める"""
    out = []
    for w in widget.winfo_children():
        try:
            t = w.cget("text")
            if t:
                out.append(str(t))
        except Exception:
            pass
        out.extend(texts(w))
    return out


try:
    import core
    import main

    app = main.App()
    app.withdraw()
    opened, warned = [], []
    real_start = os.startfile
    os.startfile = lambda p, *a: opened.append(str(p))
    main.messagebox.showwarning = lambda title, msg: warned.append(msg)
    try:
        # ---- 常設の入口 ----
        print("=== 常設ボタン ===")
        check("「英語の画像は？」ボタンがある",
              str(app._en_btn.cget("text")) == "英語の画像は？")
        check("押しても何も入れない（押す前に開いた物は無い）", not opened)

        # ---- 部品が入っている状態（このPC）----
        print()
        print("=== 部品が入っているとき ===")
        app.open_english_ocr_help()
        win = app._en_help_win
        body = " ".join(texts(win))
        check("説明の窓が開く", win.winfo_exists())
        check("入っていると分かる文が出る", "入っています" in body)
        check("崩れる理由が書いてある", "日本語だけ" in body and "NO丁" in body)
        check("2つの方法が書いてある",
              "英語（米国）" in body and "英語OCRを入れる.bat" in body)
        check("大きさの目安が書いてある", "240MB" in body and "88MB" in body)
        check("入っているので「入れる」ボタンは出さない",
              "install" not in app._en_help_buttons)
        check("言語設定を開くボタンはある", "settings" in app._en_help_buttons)
        app._en_help_buttons["settings"].invoke()
        check("言語設定を開こうとする（%s）" % opened,
              opened == ["ms-settings:regionlanguage"])
        win.destroy()

        # ---- 部品が入っていない状態（模擬）----
        print()
        print("=== 部品が入っていないとき（模擬）===")
        opened.clear()
        real_avail = core.rapidocr_available
        core.rapidocr_available = lambda: False
        try:
            app.open_english_ocr_help(reason=True)
            win = app._en_help_win
            body = " ".join(texts(win))
            check("入っていないと分かる文が出る", "入っていません" in body)
            check("「いま読んだ画像が英文だった」と前置きする",
                  "英語の文が多く" in body)
            check("「入れる」ボタンが出る", "install" in app._en_help_buttons)
            app._en_help_buttons["install"].invoke()
            check("英語OCRを入れる.bat を開こうとする（%s）"
                  % [os.path.basename(p) for p in opened],
                  len(opened) == 1 and opened[0].endswith("英語OCRを入れる.bat"))
            check("そのbatが本当にある", os.path.exists(opened[0]))
            win.destroy()

            # ---- 警告からこの案内に回るか ----
            print()
            print("=== 崩れて読まれたときの警告 ===")
            warned.clear()
            app._warn_with_english([core.OCR_ENGLISH_MISSING_MSG])
            check("英語の件だけなら、素っ気ない警告は出さない", not warned)
            check("代わりに案内の窓が開く", app._en_help_win.winfo_exists())
            app._en_help_win.destroy()

            warned.clear()
            app._warn_with_english([core.OCR_ENGLISH_MISSING_MSG,
                                    "OCR失敗 なにか.png: 理由"])
            check("他の警告はこれまでどおり出す",
                  warned == ["OCR失敗 なにか.png: 理由"])
            check("英語の件は案内の窓に回す（警告文に混ぜない）",
                  core.OCR_ENGLISH_MISSING_MSG not in " ".join(warned)
                  and app._en_help_win.winfo_exists())
            app._en_help_win.destroy()
        finally:
            core.rapidocr_available = real_avail

        # ---- 二重に開かない ----
        print()
        print("=== 二重表示の防止 ===")
        app.open_english_ocr_help()
        first = app._en_help_win
        app.open_english_ocr_help()
        check("2回押しても窓は1つ", app._en_help_win is first)
        first.destroy()
    finally:
        os.startfile = real_start
        app._stop_ticks()
        app.destroy()

    print()
    print("*** アプリ内の英語案内 %d項目すべて通過 ***" % len(ok))
finally:
    for orig, bak in keep.items():
        shutil.copy2(bak, orig)
        os.remove(bak)
    print("設定と本文を戻した")
