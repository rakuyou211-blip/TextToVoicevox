# -*- coding: utf-8 -*-
"""TextToVoicevox の起動用（Windows）。ダブルクリックで使います。

何をするか:
  1. このフォルダ（とその下）のファイルから「インターネットから来た」印
     （Zone.Identifier。ブラウザで落とした zip を展開すると全部に付く）を外す
  2. いつもの「起動.bat」を動かす（初回は部品のセットアップ、2回目からはすぐ起動）

なぜ .pyw から入るのか:
  印の付いた .bat は、Windows 11 の「スマート アプリ コントロール」が
  拡張子だけで止めます。中身も署名も評判も見ません。しかも SmartScreen と違って
  「詳細情報 → 実行」で通す道が無いので、.bat の中に置いた自己解除は
  永遠に走れませんでした（.bat が1行目に届く前に殺されるため＝鶏と卵）。

  同じ印が付いていても .py / .pyw は止められないことを実測で確かめたので、
  ここで先に印を外してから .bat を呼びます。署名を待たずに塞げます。
  2026-09-27 実測（SAC 強制オンの Windows 11 Home）:
      .bat / .cmd / .vbs / .js / .lnk  → いずれもブロック
      .py / .pyw                        → 通る

  Python が入っていないと、この .pyw はダブルクリックしても何も起きません。
  そのときは「はじめにお読みください.txt」のとおり先に Python を入れてください。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# 印を外さないフォルダ（中身は自分の機械で作った物なので印は付かない。数が多い）
SKIP_DIRS = {"venv", ".git", "__pycache__"}

TITLE = "TextToVoicevox"


def tell(message):
    """コンソールが無いので、窓で知らせる。tkinter が無ければログに落とす。"""
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
        with open(os.path.join(HERE, "起動エラー.log"), "w", encoding="utf-8") as f:
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


def main():
    if not os.path.exists(os.path.join(HERE, "main.py")):
        tell("同じフォルダにアプリの本体（main.py）が見つかりません。\n\n"
             "zip を右クリック →「すべて展開」してから、出てきたフォルダの中の\n"
             "起動.pyw を開いてください。")
        return 1

    unblock(HERE)

    bat = os.path.join(HERE, "起動.bat")
    if not os.path.exists(bat):
        tell("「起動.bat」が見つかりません。zip をもう一度展開してください。")
        return 1

    if still_blocked(bat):
        tell("ダウンロード時の印を外せませんでした。\n\n"
             "zip を右クリック → プロパティ →「許可する（ブロックの解除）」に\n"
             "チェック → OK → もう一度「すべて展開」でなおります。")
        return 1

    try:
        # 初回はセットアップの進み具合を見せたいので、黒い窓（コンソール）ありで動かす。
        # 2回目からは 起動.bat がすぐ終わるので、窓は一瞬で閉じる
        subprocess.Popen(["cmd", "/c", bat], cwd=HERE,
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    except OSError as e:
        tell("起動できませんでした: %s" % e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
