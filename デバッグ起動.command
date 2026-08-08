#!/bin/bash
# === エラー確認用の起動 (macOS): ターミナルにエラーを表示 ===
cd "$(dirname "$0")" || exit 1
# 検疫フラグ・実行権限が残っていれば静かに自己修復（直下のみ・失敗しても続行）
xattr -d com.apple.quarantine ./*.command ./*.sh 2>/dev/null
chmod +x ./*.command ./*.sh 2>/dev/null
if [ ! -x venv_mac/bin/python ]; then
    echo "venv_mac がありません。先に setup.command を実行してください。"
    read -n 1 -s -r -p "何かキーを押すと閉じます..."
    exit 1
fi
./venv_mac/bin/python main.py
echo
read -n 1 -s -r -p "終了しました。何かキーを押すと閉じます..."
