# -*- coding: utf-8 -*-
"""Windows の起動口（*.pyw）が共通で使う部分。

なぜ .pyw から入るのか:
  ブラウザで落とした zip を展開すると、中のファイル全部に「インターネットから
  来た」印（Zone.Identifier / Mark of the Web）が付く。Windows 11 の
  「スマート アプリ コントロール」は、この印が付いた .bat を、中身も署名も
  評判も見ずに拡張子だけで止める。しかも SmartScreen と違って通すボタンが無い。

  つまり .bat の中に「印を外す処理」を書いても、.bat 自身が1行目に届く前に
  止められるので永遠に走らない（鶏と卵）。

  2026-09-27 に実測したところ、同じ印が付いていても .py / .pyw は止められない。
      .bat / .cmd / .vbs / .js       → ブロック
      .lnk（署名済み py.exe を指す） → ブロック
      .py / .pyw                     → 通る
  そこで .pyw を入口にして、ここで先に印を外してから .bat を呼ぶ。順番が逆に
  なるので、行き止まりにならない。署名（SignPath）を待たなくてよい。

  Python が入っていないと .pyw はダブルクリックしても無反応になるが、
  このアプリは元々 Python が要るので、そこは案内で補う。
"""
import os
import subprocess
import sys

TITLE = "TextToVoicevox"

# 印を外さないフォルダ（自分の機械で作った物なので印は付かない。数が多い）
SKIP_DIRS = {"venv", ".git", "__pycache__"}


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


def still_blocked(path):
    """印が残っているか。残っていれば .bat はまた止められる。"""
    try:
        with open(path + ":Zone.Identifier", "rb"):
            return True
    except OSError:
        return False


def run(here, batname):
    """印を外してから、同じフォルダの batname を動かす。終了コードを返す。"""
    if not os.path.exists(os.path.join(here, "main.py")):
        tell(here,
             "同じフォルダにアプリの本体（main.py）が見つかりません。\n\n"
             "zip を右クリック →「すべて展開」してから、出てきたフォルダの中の\n"
             "起動.pyw を開いてください。")
        return 1

    unblock(here)

    bat = os.path.join(here, batname)
    if not os.path.exists(bat):
        tell(here, "「%s」が見つかりません。zip をもう一度展開してください。" % batname)
        return 1

    if still_blocked(bat):
        tell(here,
             "ダウンロード時の印を外せませんでした。\n\n"
             "zip を右クリック → プロパティ →「許可する（ブロックの解除）」に\n"
             "チェック → OK → もう一度「すべて展開」でなおります。")
        return 1

    try:
        # 初回はセットアップの進み具合を見せたいので、黒い窓（コンソール）ありで動かす。
        # 2回目からはすぐ終わるので、窓は一瞬で閉じる
        subprocess.Popen(["cmd", "/c", bat], cwd=here,
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    except OSError as e:
        tell(here, "起動できませんでした: %s" % e)
        return 1
    return 0
