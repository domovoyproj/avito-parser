@echo off
cd /d "%~dp0"
title Avito Max Parser - Windows Installer
color 0B

echo ==============================================================================
echo        AVITO MAX PARSER - WINDOWS INSTALLER
echo ==============================================================================
echo.

rem 1. Check Python
echo [*] Checking Python 3...
where py.exe >nul 2>&1
if %errorlevel% equ 0 (
    set "SYS_PYTHON=py -3"
) else (
    where python.exe >nul 2>&1
    if %errorlevel% equ 0 (
        set "SYS_PYTHON=python"
    ) else (
        color 0C
        echo [ERROR] Python not found in system PATH!
        echo Please install Python 3.10+ and check "Add Python to PATH".
        pause
        exit /b 1
    )
)

rem 2. Create Virtual Environment (.venv)
echo [*] Creating virtual environment (.venv)...
if not exist ".venv\Scripts\python.exe" (
    %SYS_PYTHON% -m venv .venv
    if %errorlevel% neq 0 (
        color 0C
        echo [ERROR] Failed to create virtual environment .venv!
        pause
        exit /b 1
    )
    echo [+] Virtual environment .venv created.
) else (
    echo [+] Virtual environment .venv already exists.
)
echo.

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "VENV_PIP=%~dp0.venv\Scripts\pip.exe"

rem 3. Upgrade pip and install requirements
echo [*] Installing requirements from requirements.txt...
"%VENV_PY%" -m pip install --upgrade pip --quiet
"%VENV_PY%" -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    color 0C
    echo [ERROR] Failed to install python dependencies!
    pause
    exit /b 1
)
echo [+] Dependencies installed successfully.
echo.

rem 4. Install Playwright Chromium
echo [*] Installing Playwright Chromium browser...
"%VENV_PY%" -m playwright install chromium
if %errorlevel% neq 0 (
    color 0E
    echo [WARN] Retrying playwright install with --with-deps...
    "%VENV_PY%" -m playwright install --with-deps chromium
)
echo [+] Playwright Chromium ready.
echo.

rem 5. Setup .env file
if not exist ".env" (
    echo [*] Creating .env from .env.example...
    copy .env.example .env >nul
    echo [+] .env created.
) else (
    echo [+] .env already exists.
)
echo.

rem 6. Setup directories
if not exist "data" mkdir data
if not exist "exports" mkdir exports
if not exist "logs" mkdir logs

color 0A
echo ==============================================================================
echo                    INSTALLATION FINISHED SUCCESSFULLY!
echo ==============================================================================
echo.
echo To start Web Panel:     start_panel.bat or start_server.bat
echo To start Telegram Bot:  start_telegram_bot.bat
echo To start everything:    start_all.bat
echo.
echo Panel URL: http://127.0.0.1:8000
echo Default login:    admin
echo Default password: admin123
echo ==============================================================================
echo.
pause
