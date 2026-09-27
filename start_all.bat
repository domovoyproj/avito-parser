@echo off
cd /d "%~dp0"
title Avito Max Parser - Full Launcher

echo ==============================================================================
echo               STARTING AVITO MAX PARSER SERVICES
echo ==============================================================================
echo.
echo [1/2] Launching Web Panel (FastAPI / Uvicorn on http://127.0.0.1:8000)...
start "Avito Max Parser - Web Server" cmd /k call "%~dp0start_server.bat"

timeout /t 2 /nobreak >nul

echo [2/2] Launching Telegram Bot Daemon...
start "Avito Max Parser - Telegram Bot" cmd /k call "%~dp0start_telegram_bot.bat"

echo.
echo [+] Services launched in separate windows.
echo Dashboard available at: http://127.0.0.1:8000
echo.
timeout /t 5 >nul
