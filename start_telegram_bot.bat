@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Avito Max Parser — Telegram Bot Daemon
color 0B

if exist ".venv\Scripts\python.exe" (
    set "PY_BIN=.venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
    set "PY_BIN=venv\Scripts\python.exe"
) else (
    echo Виртуальное окружение не найдено. Запуск установщика...
    call install_windows.bat
    if exist ".venv\Scripts\python.exe" (
        set "PY_BIN=.venv\Scripts\python.exe"
    ) else if exist "venv\Scripts\python.exe" (
        set "PY_BIN=venv\Scripts\python.exe"
    ) else (
        echo Ошибка: Python окружение не настроено.
        pause
        exit /b 1
    )
)

echo ==============================================================================
echo                   ЗАПУСК TELEGRAM-БОТА AVITO MAX PARSER
echo ==============================================================================
echo.
echo [*] Запуск демона Telegram...
echo [*] Для остановки нажмите Ctrl + C
echo.

"%PY_BIN%" telegram_bot.py
pause
