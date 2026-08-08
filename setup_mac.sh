#!/bin/bash
# === Gatekeeper に阻まれたときの入口 (macOS) ===
# 起動.command のダブルクリックが「開けません」「アクセス権がありません」になるときは、
# ターミナルに bash と半角スペースを打ち、このファイルをドラッグして return してください。
# 例: bash /Users/あなた/Downloads/TextToVoicevox/setup_mac.sh
# （bash 経由の実行は検疫ブロックの対象外。実行権限(+x)が失われていても動きます）
cd "$(dirname "$0")" || exit 1

echo "検疫フラグと実行権限を修復しています..."
xattr -dr com.apple.quarantine . 2>/dev/null
chmod +x ./*.command ./*.sh 2>/dev/null
echo "修復しました。これ以降は 起動.command などをふつうにダブルクリックで開けます。"
echo
echo "つづけて起動します（初回はセットアップが自動で走ります）。"
exec bash ./起動.command
