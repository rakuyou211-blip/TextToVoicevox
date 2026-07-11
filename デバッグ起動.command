#!/bin/bash
# === エラー確認用の起動 (macOS): ターミナルにエラーを表示 ===
cd "$(dirname "$0")" || exit 1
if [ ! -x venv_mac/bin/python ]; then
    echo "venv_mac がありません。先に setup.command を実行してください。"
    read -n 1 -s -r -p "何かキーを押すと閉じます..."
    exit 1
fi
./venv_mac/bin/python main.py
echo
read -n 1 -s -r -p "終了しました。何かキーを押すと閉じます..."
