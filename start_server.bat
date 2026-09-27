@echo off
cd /d "%~dp0"
title Avito Max Parser - Web Server

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" web_server.py
    goto :end
)

if exist "%~dp0venv\Scripts\python.exe" (
    "%~dp0venv\Scripts\python.exe" web_server.py
    goto :end
)

echo [ERROR] Virtual environment not found (.venv or venv).
echo Running install_windows.bat ...
call "%~dp0install_windows.bat"

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" web_server.py
    goto :end
)

if exist "%~dp0venv\Scripts\python.exe" (
    "%~dp0venv\Scripts\python.exe" web_server.py
    goto :end
)

echo [ERROR] Python environment setup failed.
pause
exit /b 1

:end
pause
