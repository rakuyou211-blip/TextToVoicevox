#!/bin/bash
# === アプリ起動 (macOS) ===
cd "$(dirname "$0")" || exit 1
if [ ! -x venv_mac/bin/python ]; then
    echo "venv_mac がありません。先に setup.command を実行してください。"
    read -n 1 -s -r -p "何かキーを押すと閉じます..."
    exit 1
fi
nohup ./venv_mac/bin/python main.py >/dev/null 2>&1 &
exit 0
