#!/bin/bash
# === アプリ起動 (macOS) ===
cd "$(dirname "$0")" || exit 1

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
fi

if ! venv_mac/bin/python -c "" 2>/dev/null; then
    echo
    echo "[!] セットアップが完了していないため、起動できませんでした。"
    echo "    上に出ているメッセージを確認してください。"
    read -n 1 -s -r -p "何かキーを押すと閉じます..."
    exit 1
fi
nohup ./venv_mac/bin/python main.py >/dev/null 2>&1 &
exit 0
