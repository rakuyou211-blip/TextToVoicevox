@echo off
rem === 英語の文字読み取り部品（RapidOCR）を入れる（任意） ===
rem 英文の多い画像を読むための部品です。入れなくてもアプリは使えます。
rem Windows に「英語（米国）」を追加する方法なら、アプリは大きくなりません。
chcp 65001 >nul
cd /d "%~dp0"

if not exist "%~dp0venv\Scripts\python.exe" (
    echo [!] まだセットアップが済んでいません。
    echo     先に 起動.bat を一度ダブルクリックして、アプリが開くのを確かめてから、
    echo     もう一度これを実行してください。
    pause
    exit /b 1
)

echo 英語の文字読み取り部品（RapidOCR）をインストールします。
echo   ・ダウンロードは約88MB、インストール後はアプリが約240MB大きくなります
echo   ・64bit の Windows・Python 3.12 まで対応しています
echo   ・入れなくても、Windows の［設定］→［時刻と言語］→［言語と地域］で
echo     「英語（米国）」を追加すれば、アプリを大きくせずに英語を読めます
echo.
"%~dp0venv\Scripts\python.exe" -m pip install -r "%~dp0requirements-english-ocr.txt"
if errorlevel 1 (
    echo.
    echo [!] インストールできませんでした。ネット接続を確認して、もう一度お試しください。
    pause
    exit /b 1
)

rem Python 3.13 以降などでは、対応していない部品は「入れずに成功」扱いになる。
rem 本当に入ったかを確かめてから、入ったと伝える
"%~dp0venv\Scripts\python.exe" -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('rapidocr_onnxruntime') else 1)"
if errorlevel 1 (
    echo.
    echo [!] このPCの環境では入れられませんでした（64bit の Windows・Python 3.12 までが対象です）。
    echo     Windows に「英語（米国）」を追加する方法なら、英語を読めるようになります。
    pause
    exit /b 1
)

echo.
echo 入りました。次に英語の多い画像を読むときから、自動で使います。
echo （うまく使われないときは、アプリを一度閉じて開き直してください）
pause
