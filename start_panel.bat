@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" web_server.py
) else if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" web_server.py
) else (
    echo Сначала запустите install_windows.bat
)
pause
