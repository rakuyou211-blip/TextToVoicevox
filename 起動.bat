@echo off
rem === Text-to-VOICEVOX launcher (no console window) ===
cd /d "%~dp0"
if not exist "%~dp0venv\Scripts\pythonw.exe" (
    echo venv not found. Run setup.bat first.
    pause
    exit /b 1
)
start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0main.py"
exit /b 0
