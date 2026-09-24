@echo off
rem === First-time setup: create venv and install dependencies ===
rem Requires internet ONLY for this initial setup. The app itself runs offline.
chcp 65001 >nul
cd /d "%~dp0"

rem zip を開いた中身のまま この .bat だけを実行すると、Windows は .bat だけを一時
rem フォルダへ取り出して走らせるため、隣にあるはずのファイルが無い。先に止める。
if not exist "%~dp0main.py" goto not_extracted
if not exist "%~dp0core.py" goto not_extracted
if not exist "%~dp0requirements.txt" goto not_extracted

rem ダウンロード由来の警告ブロック (Mark of the Web) をフォルダごと自己解除する。
rem SmartScreen の「詳細情報」→「実行」はこの setup.bat の初回 1 回だけで済ませ、
rem 起動.bat などの兄弟ファイルには警告を残さない。失敗しても続行（本筋はセットアップ）。
rem 対象は %~dp0（このbatのあるフォルダ）を環境変数で明示的に渡す。カレントディレクトリ
rem 依存にすると、UNCパス実行時に cd が失敗して C:\Windows を走査してしまうため。
rem venv の中は自分の機械で作った物なのでブロックは付かない。数千ファイルあるので除く。
rem 解除できたかは Zone.Identifier が残っていないかで確かめる。PowerShell が
rem 止められている環境では黙って失敗するので、成否を見てから印を置く。
echo ダウンロード時の警告ブロックを解除しています...
set "APPDIR=%~dp0"
set "UNBLOCKED="
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $f = Get-ChildItem -LiteralPath $env:APPDIR -Recurse -File -ErrorAction Stop | Where-Object { -not $_.FullName.StartsWith($env:APPDIR + 'venv\', [StringComparison]::OrdinalIgnoreCase) }; $f | Unblock-File -ErrorAction SilentlyContinue; if ($f | Get-Item -Stream Zone.Identifier -ErrorAction SilentlyContinue) { exit 1 }; exit 0 } catch { exit 1 }" >nul 2>&1
rem 異常終了（負の終了コード）は errorlevel 1 では拾えないので、0 かどうかで見る
if errorlevel 1 goto unblock_failed
if not "%ERRORLEVEL%"=="0" goto unblock_failed
set "UNBLOCKED=1"
goto unblock_done
:unblock_failed
echo [!] 警告（ブロック）は外せませんでした。毎回警告が出るときは、
echo     zip を右クリック→プロパティ→「ブロックの解除」→もう一度「すべて展開」でなおります。
:unblock_done

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

rem ドラッグ＆ドロップ部品は任意。対応する部品が無い環境（ARM64版Windowsなど）が
rem あるため requirements.txt の必須側からは外してある。ここで別に入れて、
rem 入らなくてもセットアップは止めない（アプリは D&D 無しで動く）。
echo ドラッグ＆ドロップ部品を入れています（任意）...
"%~dp0venv\Scripts\python.exe" -m pip install "tkinterdnd2>=0.3,<1"
if errorlevel 1 echo   入りませんでした。ファイルは「選ぶ」ボタンから使えます。

rem 起動.bat が二回目以降 PowerShell を呼ばずに済むよう、解除できたことを控える。
rem 版数を書いておくと、上書きで新しい版を入れたとき（venv と一緒に古い印が
rem 残っていても）版違いが分かり、起動.bat が解除をやり直せる。
if not defined UNBLOCKED goto setup_done
set "APPVER="
for /f "tokens=2 delims==" %%A in ('findstr /b /c:"APP_VERSION = " "%~dp0core.py"') do set APPVER=%%A
if not defined APPVER goto setup_done
set APPVER=%APPVER: =%
set APPVER=%APPVER:"=%
> "%~dp0venv\.unblocked" echo %APPVER%

:setup_done
echo.
echo セットアップ完了。これからは 起動.bat で起動できます。
pause
exit /b 0

:not_extracted
echo [!] 同じフォルダに main.py が見つかりません。
echo     zip を右クリック→「すべて展開」してから、出てきたフォルダの中の
echo     setup.bat を開いてください。
echo.
pause
exit /b 1
