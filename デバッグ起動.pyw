# -*- coding: utf-8 -*-
"""TextToVoicevox のデバッグ起動（Windows）。ダブルクリックで使います。

黒い画面にエラーの内容を出したまま起動します。うまく動かないときに使います。
まだ一度も起動できていない状態でも、ここから開けます（要ればセットアップもします）。
zip では app フォルダの中にあります（いちばん上には、ふだん使う「起動」だけを置くため）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# 起動.pyw と同じ探し方（隣に app フォルダがあればそこ、無ければこのフォルダ）
APP = os.path.join(HERE, "app")
if not os.path.exists(os.path.join(APP, "winlaunch.py")):
    APP = HERE
sys.path.insert(0, APP)
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
            "デバッグ起動.pyw を開いてください。")
    except Exception:
        pass
    sys.exit(1)

if __name__ == "__main__":
    sys.exit(winlaunch.run(APP, "debug"))
