#!/bin/bash
# === 初回セットアップ (macOS): venv_mac を作成し依存ライブラリをインストール ===
# この初回だけネット接続が必要。以降はオフラインで動作します。
cd "$(dirname "$0")" || exit 1

# Tk 8.6+ を含む Python を優先して使う（AppleのCLT付属PythonはTk 8.5で画面が描画されない）
PY=""
for cand in "$HOME/.local/python3.12/bin/python3" \
            /usr/local/bin/python3 /opt/homebrew/bin/python3 \
            /Library/Frameworks/Python.framework/Versions/*/bin/python3; do
    if [ -x "$cand" ]; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
    if command -v python3 >/dev/null 2>&1; then
        PY="$(command -v python3)"
        echo "[注意] Tk 8.6+ 対応の Python が見つからず $PY を使います。"
        echo "       画面が真っ白になる場合は https://www.python.org/downloads/ の Python を入れてください。"
    else
        echo "[エラー] python3 が見つかりません。"
        echo "https://www.python.org/downloads/ から Python をインストールしてください。"
        read -n 1 -s -r -p "何かキーを押すと閉じます..."
        exit 1
    fi
fi

echo "使用する Python: $PY"
echo "仮想環境を作成しています..."
"$PY" -m venv venv_mac || {
    echo "[エラー] venv の作成に失敗しました。"
    read -n 1 -s -r -p "何かキーを押すと閉じます..."
    exit 1
}

echo "依存ライブラリをインストールしています（requirements.txt）..."
./venv_mac/bin/python -m pip install --upgrade pip
./venv_mac/bin/python -m pip install -r requirements.txt || {
    echo "[エラー] インストールに失敗しました。ネット接続を確認してください。"
    read -n 1 -s -r -p "何かキーを押すと閉じます..."
    exit 1
}

echo
echo "セットアップ完了。「起動.command」で起動できます。"
read -n 1 -s -r -p "何かキーを押すと閉じます..."
