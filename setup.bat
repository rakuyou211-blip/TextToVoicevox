@echo off
rem === First-time setup: create venv and install dependencies ===
rem Requires internet ONLY for this initial setup. The app itself runs offline.
chcp 65001 >nul
cd /d "%~dp0"

echo Python を探しています...
rem 'py' ランチャを優先し、無ければ 'python' を試す。
rem （まっさらな Windows では 'python' は Microsoft Store を開くだけのダミーで、
rem   --version が失敗するので下の分岐で弾ける）
set "PYCMD="
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD (
    python --version >nul 2>&1 && set "PYCMD=python"
)

if not defined PYCMD (
    echo.
    echo [!] Python が見つかりませんでした。
    echo.
    echo     このアプリは Python が必要です。まず↓から入れてください:
    echo         https://www.python.org/downloads/
    echo.
    echo     ・バージョンは 3.9 以降
    echo     ・インストール画面の下にある "Add python.exe to PATH" に
    echo       必ずチェックを入れてください（ここが一番のつまずきどころです）
    echo.
    echo     入れ終わったら、この setup.bat をもう一度ダブルクリックしてください。
    echo.
    pause
    exit /b 1
)

echo 使用する Python: %PYCMD%
echo 仮想環境（venv）を作成しています...
%PYCMD% -m venv "%~dp0venv"
if errorlevel 1 (
    echo.
    echo [ERROR] 仮想環境の作成に失敗しました。
    echo     Python 3.9 以降が正しく入っているか確認してください:
    echo         https://www.python.org/downloads/
    echo     （インストール時の "Add python.exe to PATH" のチェックも確認）
    pause
    exit /b 1
)

echo 必要ライブラリをインストールしています（requirements.txt）...
"%~dp0venv\Scripts\python.exe" -m pip install --upgrade pip
"%~dp0venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo [ERROR] ライブラリのインストールに失敗しました。
    echo     ネット接続を確認して、もう一度 setup.bat を実行してください。
    pause
    exit /b 1
)
echo.
echo セットアップ完了。これからは 起動.bat で起動できます。
pause
