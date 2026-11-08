# -*- coding: utf-8 -*-
"""TextToVoicevox の起動用（Windows）。ダブルクリックで使います。

初回は黒い窓で部品を入れてから（数分）、2回目からはすぐにアプリを開きます。
1回開くと、隣にアプリの絵の「TextToVoicevox」ができるので、次からはそちらを開けます。
.bat を使わないのは、Windows に止められないためです（理由は winlaunch.py）。

Python が入っていないと、これはダブルクリックしても何も起きません。
そのときは「はじめにお読みください.txt」のとおり、先に Python を入れてください。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# zip では、プログラムや部品は隣の app フォルダに入っている（開いたときに見える物を減らすため）。
# GitHub の中身と1行導入の入れ先では、同じフォルダにある
APP = os.path.join(HERE, "app")
if not os.path.exists(os.path.join(APP, "winlaunch.py")):
    APP = HERE
sys.path.insert(0, APP)
try:
    import winlaunch
except ImportError:
    # zip を展開せずに中から開くと、Windows はこのファイルだけを一時フォルダへ
    # 取り出して動かすので、隣の app フォルダが無い
    try:
        import tkinter
        from tkinter import messagebox
        tkinter.Tk().withdraw()
        messagebox.showwarning(
            "TextToVoicevox",
            "zip の中から直接開いています。\n\n"
            "zip を右クリック →「すべて展開」してから、出てきたフォルダの中の\n"
            "起動.pyw を開いてください。")
    except Exception:
        pass
    sys.exit(1)

if __name__ == "__main__":
    sys.exit(winlaunch.run(APP, "launch"))
