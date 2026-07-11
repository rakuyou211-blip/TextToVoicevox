@echo off
rem === Debug launcher (console stays open, shows errors) ===
chcp 65001 >nul
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" "%~dp0main.py"
echo.
echo ---- exited ----
pause
