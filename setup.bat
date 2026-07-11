@echo off
rem === First-time setup: create venv and install dependencies ===
rem Requires internet ONLY for this initial setup. The app itself runs offline.
chcp 65001 >nul
cd /d "%~dp0"
echo Creating virtual environment...
python -m venv "%~dp0venv"
if errorlevel 1 (
    echo [ERROR] Failed to create venv. Is Python installed and on PATH?
    pause
    exit /b 1
)
echo Installing dependencies (pypdfium2, pillow, requests, tkinterdnd2)...
"%~dp0venv\Scripts\python.exe" -m pip install --upgrade pip
"%~dp0venv\Scripts\python.exe" -m pip install pypdfium2 pillow requests tkinterdnd2
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
echo.
echo Setup complete. You can now launch with 起動.bat
pause
