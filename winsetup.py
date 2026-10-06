# -*- coding: utf-8 -*-
"""Windows の黒い窓で動く部分（初回セットアップ・デバッグ起動・英語OCRの導入）。

自分では開かない。起動.pyw / デバッグ起動.pyw / 英語OCRを入れる.pyw が、
ダウンロードの印を外してから、黒い窓（コンソール）でこれを動かす。
v1.23.1 までは .bat でやっていたことを、止められない Python に移した
（理由は winlaunch.py の先頭）。

  python winsetup.py launch   初回セットアップ → アプリを開く
  python winsetup.py debug    （要ればセットアップ →）エラーを画面に出したまま起動
  python winsetup.py ocr      英語の文字読み取り部品（RapidOCR）を入れる

Python は 3.9 以降が必要（画面の部品 tkinter も要る）。
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import winlaunch  # noqa: E402

MIN_PYTHON = (3, 9)
PIP = ["-m", "pip", "install", "--disable-pip-version-check"]


def say(text=""):
    print(text, flush=True)


def wait_close():
    try:
        input("\nEnter キーを押すと閉じます…")
    except (EOFError, KeyboardInterrupt):
        pass


def python_usable():
    """この Python で venv を作れるか。だめなら理由を出して False。"""
    if sys.version_info < MIN_PYTHON:
        say("[!] Python %d.%d では動きません（3.9 以降が必要です）。" % sys.version_info[:2])
        say("    https://www.python.org/downloads/ から新しい Python を入れてください。")
        say("    インストール画面の下にある \"Add python.exe to PATH\" にも")
        say("    チェックを入れてください。")
        return False
    try:
        import tkinter  # noqa: F401
    except ImportError:
        say("[!] この Python には画面の部品（tkinter）が入っていません。")
        say("    https://www.python.org/downloads/ の公式版を入れ直してください")
        say("    （インストール画面の「tcl/tk and IDLE」にチェック）。")
        return False
    return True


def pip_works(py):
    try:
        return subprocess.call([py, "-m", "pip", "--version"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0
    except OSError:
        return False


def setup():
    """venv を作って部品を入れる。成功なら True。"""
    py, _ = winlaunch.venv_exes(HERE)
    venv_dir = os.path.join(HERE, "venv")
    say("初回セットアップをしています。")
    say("（初回だけネット接続が必要です。数分かかることがあります）")
    say()
    if not python_usable():
        return False

    if winlaunch.venv_ok(HERE):
        # venv を作る途中（pip を入れる前）で窓を閉じた・ウイルス対策に止められた、など。
        # 見た目は揃っていても pip が無いので、作り直さないと何度開いても進めない
        need_new = not pip_works(py)
        if need_new:
            say("作りかけの venv があったので、作り直します。")
    else:
        need_new = True
        if os.path.exists(venv_dir):
            # 別のPCから持ってきた・Python を入れ直した venv は動かないので作り直す
            say("今のパソコンでは動かない venv があったので、作り直します。")
    if need_new:
        say("仮想環境（venv）を作っています…")
        if subprocess.call([sys.executable, "-m", "venv", "--clear", venv_dir]) != 0:
            say()
            say("[ERROR] 仮想環境の作成に失敗しました。")
            say("    Python 3.9 以降が正しく入っているか確認してください:")
            say("        https://www.python.org/downloads/")
            return False

    say("必要な部品を入れています（requirements.txt）…")
    if subprocess.call([py] + PIP + ["-r", os.path.join(HERE, "requirements.txt")]) != 0:
        say()
        say("[ERROR] 部品のインストールに失敗しました。")
        say("    ネット接続を確認して、もう一度 起動.pyw を開いてください。")
        say("    何度やっても同じなら、このフォルダの「venv」フォルダを消してから開くと、")
        say("    最初から作り直します。")
        return False

    # ドラッグ＆ドロップ部品は任意。対応する部品が無い環境（ARM64版Windowsなど）が
    # あるため requirements.txt の必須側からは外してある。入らなくても止めない
    say("ドラッグ＆ドロップ部品を入れています（任意）…")
    if subprocess.call([py] + PIP + ["tkinterdnd2>=0.3,<1"]) != 0:
        say("  入りませんでした。ファイルは「選ぶ」ボタンから使えます。")

    # ここまで来たら済んだ印を置く（途中で窓を閉じたら、次の起動でやり直す）
    try:
        with open(os.path.join(venv_dir, winlaunch.STAMP_NAME), "w", encoding="utf-8") as f:
            f.write(winlaunch.wanted_stamp(HERE))
    except OSError:
        pass
    return True


def do_launch():
    if not winlaunch.setup_done(HERE) and not setup():
        say()
        say("[!] セットアップが完了していないため、起動できませんでした。")
        say("    上に出ているメッセージを確認してください。")
        wait_close()
        return 1
    say()
    say("セットアップ完了。アプリを開きます。")
    winlaunch.start_app(HERE)
    time.sleep(2)  # 完了の一言を読めるくらいは窓を残す
    return 0


def do_debug():
    if not winlaunch.setup_done(HERE) and not setup():
        wait_close()
        return 1
    py, _ = winlaunch.venv_exes(HERE)
    say("デバッグ起動: エラーが起きたら、この窓に内容が出ます。")
    say()
    code = subprocess.call([py, os.path.join(HERE, "main.py")], cwd=HERE)
    say()
    say("---- 終了しました（終了コード %d）----" % code)
    wait_close()
    return 0


def do_ocr():
    py, _ = winlaunch.venv_exes(HERE)
    # 見るのは venv が動くかだけ（済んだ印は見ない）。1行導入（install.ps1）で入れた人の
    # venv は、起動.pyw を通らないので印が無いことがある
    if not winlaunch.venv_ok(HERE):
        say("[!] まだセットアップが済んでいません。")
        say("    先に 起動.pyw を一度ダブルクリックして、アプリが開くのを確かめてから、")
        say("    もう一度これを開いてください。")
        wait_close()
        return 1
    say("英語の文字読み取り部品（RapidOCR）をインストールします。")
    say("  ・ダウンロードは約88MB、インストール後はアプリが約240MB大きくなります")
    say("  ・64bit の Windows・Python 3.12 まで対応しています")
    say("  ・入れなくても、Windows の［設定］→［時刻と言語］→［言語と地域］で")
    say("    「英語（米国）」を追加すれば、アプリを大きくせずに英語を読めます")
    say()
    req = os.path.join(HERE, "requirements-english-ocr.txt")
    if subprocess.call([py] + PIP + ["-r", req]) != 0:
        say()
        say("[!] インストールできませんでした。ネット接続を確認して、もう一度お試しください。")
        wait_close()
        return 1
    # Python 3.13 以降などでは、対応していない部品は「入れずに成功」扱いになる。
    # 本当に入ったかを確かめてから、入ったと伝える
    check = ("import importlib.util, sys; "
             "sys.exit(0 if importlib.util.find_spec('rapidocr_onnxruntime') else 1)")
    if subprocess.call([py, "-c", check]) != 0:
        say()
        say("[!] このPCの環境では入れられませんでした（64bit の Windows・Python 3.12 までが対象です）。")
        say("    Windows に「英語（米国）」を追加する方法なら、英語を読めるようになります。")
        wait_close()
        return 1
    say()
    say("入りました。次に英語の多い画像を読むときから、自動で使います。")
    say("（うまく使われないときは、アプリを一度閉じて開き直してください）")
    wait_close()
    return 0


def main(argv):
    mode = argv[1] if len(argv) > 1 else "launch"
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW("TextToVoicevox")
    except Exception:
        pass
    handler = {"launch": do_launch, "debug": do_debug, "ocr": do_ocr}.get(mode)
    if handler is None:
        say("使い方: python winsetup.py [launch|debug|ocr]")
        return 2
    try:
        return handler()
    except Exception as e:  # 想定外でも窓が一瞬で消えないように
        say()
        say("[ERROR] 想定外のエラー: %r" % (e,))
        wait_close()
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
