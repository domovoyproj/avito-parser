@echo off
chcp 65001 >nul
title Avito Max Parser — Telegram Bot Daemon
color 0B

if not exist "venv" (
    echo [!] Виртуальное окружение не найдено. Запуск автоустановщика...
    call install_windows.bat
)

call venv\Scripts\activate.bat

echo ==============================================================================
echo                   ЗАПУСК TELEGRAM-БОТА AVITO MAX PARSER
echo ==============================================================================
echo.
echo [*] Запуск демона Telegram...
echo [*] Для остановки нажмите Ctrl + C
echo.

python telegram_bot.py
pause
