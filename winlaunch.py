# -*- coding: utf-8 -*-
"""Windows の起動口（*.pyw）が共通で使う部分。

なぜ .bat を1つも使わないのか:
  ブラウザで落とした zip を展開すると、中のファイル全部に「インターネットから
  来た」印（Zone.Identifier / Mark of the Web）が付く。Windows 11 の
  「スマート アプリ コントロール」は、この印が付いた .bat を、中身も署名も
  評判も見ずに拡張子だけで止める。しかも SmartScreen と違って通すボタンが無い。

  2026-09-27 に実測したところ、同じ印が付いていても .py / .pyw は止められない。
      .bat / .cmd / .vbs / .js       → ブロック
      .lnk（署名済み py.exe を指す） → ブロック
      .py / .pyw                     → 通る

  v1.23 では .pyw で印を外してから .bat を呼んでいたが、zip には「起動.bat」と
  「起動.pyw」が並んでいて、拡張子を隠す Windows の既定では、どちらも「起動」に
  見える。.bat の方を選んだ人は止められる（2026-10-03、本人の実機で再発）。
  そこで v1.24.0 から、セットアップも起動も Python だけでやる。止められる入口が
  zip に1つも無ければ、どれを開いても止められない。

  2回目からは cmd も PowerShell も通さず、venv の pythonw で main.py を直接開く。
  初回（と、部品の入れ直しが要るとき）だけ、黒い窓で winsetup.py を動かして
  進み具合を見せる。

  Python が入っていないと .pyw はダブルクリックしても無反応になるが、
  このアプリは元々 Python が要るので、そこは案内で補う。
"""
import hashlib
import os
import subprocess
import sys

TITLE = "TextToVoicevox"

# 印を外さないフォルダ（自分の機械で作った物なので印は付かない。数が多い）
SKIP_DIRS = {"venv", ".git", "__pycache__"}

# v1.23.1 までの zip に入っていた .bat。上書きで展開すると残り、また「起動」が
# 2つ並んでしまうので、見つけたら消す（中身はどれも今は使っていない）
LEGACY_BATS = ("起動.bat", "setup.bat", "デバッグ起動.bat", "英語OCRを入れる.bat")

# セットアップが済んだ印。requirements.txt の中身を控えておき、新しい版で
# 必要な部品が変わったときだけ入れ直す
STAMP_NAME = ".t2v_setup"

DETACHED = (getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)


def tell(here, message):
    """コンソールが無いので窓で知らせる。tkinter が無ければログに落とす。"""
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showwarning(TITLE, message)
        root.destroy()
        return
    except Exception:
        pass
    try:
        with open(os.path.join(here, "起動エラー.log"), "w", encoding="utf-8") as f:
            f.write(message + "\n")
    except OSError:
        pass
    sys.stderr.write(message + "\n")


def unblock(root):
    """Zone.Identifier（ダウンロードの印）を消す。消せなくても止めない。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            try:
                os.remove(os.path.join(dirpath, name) + ":Zone.Identifier")
            except OSError:
                pass  # 印が無い・読めない。できたところまでで十分


def remove_legacy(here):
    """前の版の .bat を消す。消せなくても止めない。"""
    for name in LEGACY_BATS:
        try:
            os.remove(os.path.join(here, name))
        except OSError:
            pass


def venv_exes(here):
    """venv の (python.exe, pythonw.exe)。"""
    scripts = os.path.join(here, "venv", "Scripts")
    return os.path.join(scripts, "python.exe"), os.path.join(scripts, "pythonw.exe")


def venv_ok(here):
    """venv が今のパソコンで動くか。フォルダごと別のPCから持ってきたときや、
    Python を入れ直したときは、元の Python が無くて動かない（venv は機械ごとに
    作る物）。pyvenv.cfg の home に python.exe があるかで見る（起動のたびに
    Python を1回走らせるより速い）。"""
    py, pyw = venv_exes(here)
    if not (os.path.exists(py) and os.path.exists(pyw)):
        return False
    try:
        with open(os.path.join(here, "venv", "pyvenv.cfg"), encoding="utf-8") as f:
            for line in f:
                key, _, value = line.partition("=")
                if key.strip().lower() == "home":
                    return os.path.exists(os.path.join(value.strip(), "python.exe"))
    except OSError:
        return False
    return False


def wanted_stamp(here):
    try:
        with open(os.path.join(here, "requirements.txt"), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def setup_done(here):
    """venv が動き、今の requirements.txt で部品を入れ終わっているか。"""
    if not venv_ok(here):
        return False
    try:
        with open(os.path.join(here, "venv", STAMP_NAME), encoding="utf-8") as f:
            return f.read().strip() == wanted_stamp(here)
    except OSError:
        return False


def console_python():
    """黒い窓で動かす python.exe（.pyw は pythonw.exe で動いているので隣を探す）。"""
    exe = sys.executable or "python"
    cand = os.path.join(os.path.dirname(exe), "python.exe")
    return cand if os.path.exists(cand) else exe


def start_app(here):
    """venv の pythonw で main.py を開く（窓なし・この起動口とは切り離す）。"""
    _, pyw = venv_exes(here)
    subprocess.Popen([pyw, os.path.join(here, "main.py")], cwd=here,
                     creationflags=DETACHED, close_fds=True)


def open_console(here, mode):
    """黒い窓で winsetup.py を動かす（セットアップ・デバッグ起動・英語OCR）。"""
    subprocess.Popen([console_python(), os.path.join(here, "winsetup.py"), mode],
                     cwd=here, creationflags=NEW_CONSOLE)


def run(here, mode):
    """mode: "launch"（起動）/ "debug"（デバッグ起動）/ "ocr"（英語OCRを入れる）。
    印を外してから動かす。終了コードを返す。"""
    if not (os.path.exists(os.path.join(here, "main.py"))
            and os.path.exists(os.path.join(here, "winsetup.py"))):
        tell(here,
             "同じフォルダにアプリの本体（main.py）が見つかりません。\n\n"
             "zip を右クリック →「すべて展開」してから、出てきたフォルダの中の\n"
             "起動.pyw を開いてください。")
        return 1

    unblock(here)
    remove_legacy(here)

    try:
        if mode == "launch" and setup_done(here):
            start_app(here)
        else:
            open_console(here, mode)
    except OSError as e:
        tell(here, "起動できませんでした: %s" % e)
        return 1
    return 0
