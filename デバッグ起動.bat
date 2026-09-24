@echo off
rem === Debug launcher (console stays open, shows errors) ===
chcp 65001 >nul
cd /d "%~dp0"

rem zip を開いた中身のまま この .bat だけを実行すると、Windows は .bat だけを一時
rem フォルダへ取り出して走らせるため、隣にあるはずのファイルが無い。先に止める。
if not exist "%~dp0main.py" goto not_extracted
if not exist "%~dp0core.py" goto not_extracted

rem ダウンロード由来の警告ブロックが残っていれば解除（直下のみ・失敗しても続行）。
rem こちらは不具合を見るための入口なので、印は見ないし置かない（毎回やり直す）。
set "APPDIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $f = Get-ChildItem -LiteralPath $env:APPDIR -File -ErrorAction Stop; $f | Unblock-File -ErrorAction SilentlyContinue; if ($f | Get-Item -Stream Zone.Identifier -ErrorAction SilentlyContinue) { exit 1 }; exit 0 } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 goto unblock_done
echo [!] ダウンロード時の警告（ブロック）は外せませんでした。毎回警告が出るときは、
echo     zip を右クリック→プロパティ→「ブロックの解除」→もう一度「すべて展開」でなおります。
echo.

:unblock_done
if not exist "%~dp0venv\Scripts\python.exe" (
    echo venv がありません。先に setup.bat を実行してください。
    echo （ふだんの 起動.bat なら、初回セットアップも自動でやります）
    pause
    exit /b 1
)
"%~dp0venv\Scripts\python.exe" "%~dp0main.py"
echo.
echo ---- exited ----
pause
exit /b 0

:not_extracted
echo [!] 同じフォルダに main.py が見つかりません。
echo     zip を右クリック→「すべて展開」してから、出てきたフォルダの中の
echo     デバッグ起動.bat を開いてください。
echo.
pause
exit /b 1
