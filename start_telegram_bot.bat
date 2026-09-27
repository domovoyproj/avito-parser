@echo off
cd /d "%~dp0"
title Avito Max Parser - Telegram Bot

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" telegram_bot.py
    goto :end
)

if exist "%~dp0venv\Scripts\python.exe" (
    "%~dp0venv\Scripts\python.exe" telegram_bot.py
    goto :end
)

echo [ERROR] Virtual environment not found (.venv or venv).
echo Running install_windows.bat ...
call "%~dp0install_windows.bat"

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" telegram_bot.py
    goto :end
)

if exist "%~dp0venv\Scripts\python.exe" (
    "%~dp0venv\Scripts\python.exe" telegram_bot.py
    goto :end
)

echo [ERROR] Python environment setup failed.
pause
exit /b 1

:end
pause
