# -*- coding: utf-8 -*-
"""TextToVoicevox の起動用（Windows）。ダブルクリックで使います。

ダウンロードの印を外してから「起動.bat」を動かします。
なぜ .bat を直接ではなく .pyw から入るのかは winlaunch.py に書いてあります。

Python が入っていないと、これはダブルクリックしても何も起きません。
そのときは「はじめにお読みください.txt」のとおり、先に Python を入れてください。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winlaunch

if __name__ == "__main__":
    sys.exit(winlaunch.run(os.path.dirname(os.path.abspath(__file__)), "起動.bat"))
