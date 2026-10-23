#!/bin/bash
# === 初回セットアップ (macOS): venv_mac を作成し依存ライブラリをインストール ===
# この初回だけネット接続が必要。以降はオフラインで動作します。
cd "$(dirname "$0")" || exit 1

# ダウンロード検疫フラグ（com.apple.quarantine）と実行権限を、フォルダごと自己修復する。
# Gatekeeper の承認（macOS 15以降: システム設定→「このまま開く」／14以前: 右クリック→「開く」）
# はこの setup.command の初回 1 回だけで済ませ、起動.command などの兄弟ファイルは
# 以降ふつうのダブルクリックで開けるようにする。失敗しても続行（本筋はセットアップ）。
echo "ダウンロード時の検疫フラグと実行権限を整えています..."
xattr -dr com.apple.quarantine . 2>/dev/null
chmod +x ./*.command ./*.sh 2>/dev/null

# 画面表示の部品（tkinter）が本当に動く Python だけを使う。
#   - Homebrew の python3 は tkinter が別売り（python-tk）で、入っていないとアプリが
#     何も言わずに起動しない（2026-10-03 の配布後チェックで指摘）
#   - Apple の開発ツール付属の Python は Tk 8.5 で、画面が真っ白になる
# なので「ある」だけでは選ばず、Tcl を実際に動かし、Tk 8.6 以上・Python 3.9 以上かを確かめる。
# 起動.command も同じ確かめ方をする（ずれると、作った venv を起動側が弾いてしまう）。
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
py_ok() {
    [ -x "$1" ] || return 1
    "$1" -c "$T2V_TK_CHECK" >/dev/null 2>&1
}

PY=""
if [ -n "${T2V_PY_CANDIDATES:-}" ]; then
    # テスト用: 候補を「:」区切りで差し替える（PATH 上の python3 も見ない）
    IFS=: read -r -a cands <<< "$T2V_PY_CANDIDATES"
else
    cands=("$HOME/.local/python3.12/bin/python3"
           /usr/local/bin/python3 /opt/homebrew/bin/python3
           /Library/Frameworks/Python.framework/Versions/*/bin/python3)
    # PATH 上の python3 も最後に試す。/usr/bin/python3 は開発ツールが無いと
    # 「インストールしますか」の窓を出すだけなので、開発ツールがあるときだけ
    c="$(command -v python3 2>/dev/null)"
    if [ -n "$c" ] && { [ "$c" != /usr/bin/python3 ] || xcode-select -p >/dev/null 2>&1; }; then
        cands+=("$c")
    fi
fi
for cand in "${cands[@]}"; do
    # venv_mac の中の Python は、これから作り直す当の物なので使わない
    case "$cand" in "$PWD"/venv_mac/*) continue ;; esac
    if [ -x "$cand" ]; then
        if py_ok "$cand"; then PY="$cand"; break; fi
        echo "  （${cand} は画面表示の部品 tkinter / Tk 8.6 以上が使えないので見送ります）"
    fi
done
if [ -z "$PY" ]; then
    echo
    echo "[エラー] このアプリの画面を出せる Python が見つかりませんでした。"
    echo "  画面表示に tkinter（Tk 8.6 以上）を使います。次のどちらかを入れてから、"
    echo "  もう一度 setup.command を開いてください。"
    echo
    echo "  1. python.org の Python（おすすめ・tkinter が最初から入っています）"
    echo "     https://www.python.org/downloads/ の「macOS 64-bit universal2 installer」"
    echo "  2. Homebrew を使っている場合は、tkinter の部品を足す:"
    echo "     brew install python-tk"
    echo
    read -n 1 -s -r -p "何かキーを押すと閉じます..."
    exit 1
fi

echo "使用する Python: $PY"
echo "仮想環境を作成しています..."
# venv_mac は今の Python で、空の所から作り直す（tkinter の無い Python で作った・
# 別の Mac から持ってきた等の venv が残っていても、作り直しが効くように）。
# ただし前の venv_mac は、新しいのが出来上がるまで venv_mac.old に退かしておき、
# 部品のダウンロードに失敗したら（オフライン等）元に戻す。動いていた venv を消さないため
rm -rf venv_mac.old
[ -e venv_mac ] && mv venv_mac venv_mac.old
restore_old_venv() {
    rm -rf venv_mac
    if [ -e venv_mac.old ]; then
        mv venv_mac.old venv_mac
        echo "  （前の venv_mac に戻しました）"
    fi
}
"$PY" -m venv venv_mac || {
    restore_old_venv
    echo "[エラー] venv の作成に失敗しました。"
    read -n 1 -s -r -p "何かキーを押すと閉じます..."
    exit 1
}

echo "依存ライブラリをインストールしています（requirements.txt）..."
./venv_mac/bin/python -m pip install --upgrade pip
./venv_mac/bin/python -m pip install -r requirements.txt || {
    restore_old_venv
    echo "[エラー] インストールに失敗しました。ネット接続を確認してください。"
    read -n 1 -s -r -p "何かキーを押すと閉じます..."
    exit 1
}
rm -rf venv_mac.old

echo
echo "セットアップ完了。「起動.command」で起動できます。"
read -n 1 -s -r -p "何かキーを押すと閉じます..."
exit 0
