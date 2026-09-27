@echo off
cd /d "%~dp0"
title Avito Max Parser - Web Panel

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" web_server.py
    goto :end
)

if exist "%~dp0venv\Scripts\python.exe" (
    "%~dp0venv\Scripts\python.exe" web_server.py
    goto :end
)

echo [ERROR] Virtual environment not found (.venv or venv).
echo Please run install_windows.bat first.
pause
exit /b 1

:end
pause
