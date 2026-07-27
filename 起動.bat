@echo off
rem === Text-to-VOICEVOX launcher (no console window) ===
chcp 65001 >nul
cd /d "%~dp0"
if not exist "%~dp0venv\Scripts\pythonw.exe" (
    echo 初回セットアップがまだのようなので、先に setup.bat を実行します。
    echo （初回だけネット接続が必要です。数分かかることがあります）
    echo.
    call "%~dp0setup.bat"
) else (
    rem venv があっても、フォルダごと別のPCから持ってきた場合や Python を
    rem 入れ直した場合は、中の Python が動かない（venv は機械ごとに作る物）。
    rem 実際に動くか一度だけ確かめて、ダメなら setup.bat で作り直す。
    "%~dp0venv\Scripts\python.exe" -c "" >nul 2>&1
    if errorlevel 1 (
        echo venv が今のパソコンでは動かないため、setup.bat で作り直します。
        echo.
        call "%~dp0setup.bat"
    )
)
if not exist "%~dp0venv\Scripts\pythonw.exe" (
    echo.
    echo [!] セットアップが完了していないため、起動できませんでした。
    echo     上に出ているメッセージを確認してください。
    pause
    exit /b 1
)
start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0main.py"
exit /b 0
