#!/bin/bash
# === アプリ起動 (macOS) ===
cd "$(dirname "$0")" || exit 1
# 検疫フラグ・実行権限が残っていれば静かに自己修復（直下のみ・失敗しても続行）
xattr -d com.apple.quarantine ./*.command ./*.sh 2>/dev/null
chmod +x ./*.command ./*.sh 2>/dev/null

# venv_mac の Python で、画面表示の部品（tkinter / Tk 8.6 以上）が本当に動くか。
# setup.command の py_ok と同じ確かめ方。ここを通らないまま main.py を起動すると、
# 窓が出ないまま何も言わずに終わってしまう（Homebrew の tkinter 無し Python で作った等）
# 確かめ方の中身（setup.command・起動.command・install.sh でまったく同じ文にしておく。
# ずれると、片方で作った venv をもう片方が弾く。tests/test_mac_setup.py が見張っている）。
# venv の中の Python では、まず main.py の _fix_tcl_library と同じ手当てをする: 1行導入が
# 入れる自分用 Python（python-build-standalone）から作った venv は、そのままだと Tcl が
# 自分の部品（init.tcl）を venv の中に探しに行って見つけられず、本当は動くのに落ちる。
T2V_TK_CHECK='import glob, os, sys
if sys.platform == "darwin" and sys.prefix != sys.base_prefix:
    for var, pat, marker in (("TCL_LIBRARY", "tcl8.*", "init.tcl"), ("TK_LIBRARY", "tk8.*", "tk.tcl")):
        if not os.environ.get(var):
            for d in sorted(glob.glob(os.path.join(sys.base_prefix, "lib", pat)), reverse=True):
                if os.path.isfile(os.path.join(d, marker)):
                    os.environ[var] = d
                    break
import tkinter
tkinter.Tcl().eval("info patchlevel")
sys.exit(0 if sys.version_info >= (3, 9) and tkinter.TkVersion >= 8.6 else 1)'
venv_ok() {
    [ -x venv_mac/bin/python ] || return 1
    venv_mac/bin/python -c "$T2V_TK_CHECK" >/dev/null 2>&1
}

if [ ! -x venv_mac/bin/python ]; then
    echo "初回セットアップがまだのようなので、先に setup.command を実行します。"
    echo "（初回だけネット接続が必要です。数分かかることがあります）"
    echo
    bash ./setup.command
elif ! venv_mac/bin/python -c "" 2>/dev/null; then
    # venv があっても、フォルダごと別の Mac から持ってきた場合や Python を
    # 入れ直した場合は、中の Python が動かない（venv は機械ごとに作る物）。
    echo "venv_mac が今の環境では動かないため、setup.command で作り直します。"
    echo
    bash ./setup.command
elif ! venv_ok; then
    echo "venv_mac の Python では、画面表示の部品（tkinter / Tk 8.6 以上）が使えません。"
    echo "このままでは窓が出ないので、setup.command で作り直します。"
    echo
    bash ./setup.command
fi

if ! venv_ok; then
    echo
    echo "[!] セットアップが完了していないため、起動できませんでした。"
    echo "    上に出ているメッセージを確認してください。"
    echo "    画面表示の部品（tkinter）が無いときは、python.org の Python"
    echo "    （https://www.python.org/downloads/ ）か、Homebrew なら brew install python-tk"
    echo "    を入れてから、setup.command をもう一度開いてください。"
    read -n 1 -s -r -p "何かキーを押すと閉じます..."
    exit 1
fi
nohup ./venv_mac/bin/python main.py >/dev/null 2>&1 &
exit 0
