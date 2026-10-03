# -*- coding: utf-8 -*-
"""英語の文字読み取り部品（RapidOCR）を入れる（Windows・任意）。ダブルクリックで使います。

英文の多い画像を読むための部品です。入れなくてもアプリは使えます。
Windows に「英語（米国）」を追加する方法なら、アプリは大きくなりません。
先に 起動.pyw でアプリが開くのを確かめてから使ってください。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import winlaunch
except ImportError:
    # zip を展開せずに中から開くと、Windows はこのファイルだけを一時フォルダへ
    # 取り出して動かすので、隣の winlaunch.py が無い
    try:
        import tkinter
        from tkinter import messagebox
        tkinter.Tk().withdraw()
        messagebox.showwarning(
            "TextToVoicevox",
            "zip の中から直接開いています。\n\n"
            "zip を右クリック →「すべて展開」してから、出てきたフォルダの中の\n"
            "英語OCRを入れる.pyw を開いてください。")
    except Exception:
        pass
    sys.exit(1)

if __name__ == "__main__":
    sys.exit(winlaunch.run(HERE, "ocr"))
