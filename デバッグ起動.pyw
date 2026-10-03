# -*- coding: utf-8 -*-
"""TextToVoicevox のデバッグ起動（Windows）。ダブルクリックで使います。

黒い画面にエラーの内容を出したまま起動します。
「起動.pyw」と同じく、先にダウンロードの印を外してから「デバッグ起動.bat」を
動かすので、まだ一度も起動できていない状態からでも、ここから調べられます。
（印の付いた .bat は、スマート アプリ コントロールに止められて動きません）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winlaunch

if __name__ == "__main__":
    sys.exit(winlaunch.run(os.path.dirname(os.path.abspath(__file__)), "デバッグ起動.bat"))
