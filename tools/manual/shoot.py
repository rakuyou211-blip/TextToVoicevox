# -*- coding: utf-8 -*-
"""説明書の画像を、本物のアプリの画面から撮り直す（Windows 専用）。

    python tools/manual/shoot.py [--settings 声などを借りる settings.json] [--shots 撮った画面を置くフォルダ]

1. capture.py を見えないデスクトップで動かして、アプリの画面を撮る（使っている人の画面に
   窓を出さない。マウスやキーボードも奪わない）
2. make_images.py で切り抜いて縮め、docs/manual/img/ の JPEG を書き換える
3. tools/make_manual.py で、配る説明書を作り直す

動かす Python は、アプリの部品（requirements.txt）が入ったもの（アプリの venv など）。
VOICEVOX が使える状態で動かす。撮り終えたら、画像の差分を目で確かめてから commit する。
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import capture  # noqa: E402
import hidden_desktop  # noqa: E402
import make_images  # noqa: E402
import make_manual  # noqa: E402


def _opt(args, name):
    if name in args:
        i = args.index(name)
        value = args[i + 1]
        del args[i:i + 2]
        return value
    return None


def main(argv):
    args = list(argv[1:])
    settings = _opt(args, "--settings")
    shots = _opt(args, "--shots") or tempfile.mkdtemp(prefix="t2v_manual_shots_")
    os.makedirs(shots, exist_ok=True)
    cmd = subprocess.list2cmdline([sys.executable, os.path.join(HERE, "capture.py"), shots]
                                  + ([os.path.abspath(settings)] if settings else []))
    print("撮っています（見えないデスクトップで。1〜2分）…", shots)
    hidden_desktop.run(ROOT, cmd)
    with open(os.path.join(shots, "capture.log"), encoding="utf-8") as f:
        log = f.read()
    missing = [n for n in capture.SHOTS + ("boxes.json",) if not os.path.exists(os.path.join(shots, n))]
    if missing:
        print(log)
        print("撮れなかった物があります:", ", ".join(missing))
        return 1
    make_images.main(shots)
    make_manual.main()
    print("できました。画像の差分を目で確かめてください: git status docs/manual/img")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
