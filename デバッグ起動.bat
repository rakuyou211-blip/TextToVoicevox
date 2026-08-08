@echo off
rem === Debug launcher (console stays open, shows errors) ===
chcp 65001 >nul
cd /d "%~dp0"
rem ダウンロード由来の警告ブロックが残っていれば静かに解除（直下のみ・失敗しても続行）
set "APPDIR=%~dp0"
powershell -NoProfile -Command "Get-ChildItem -LiteralPath $env:APPDIR -File | Unblock-File -ErrorAction SilentlyContinue" >nul 2>&1
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
