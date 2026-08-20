@echo off
chcp 65001 >nul
title Avito Max Parser — Web Control Terminal
color 0A

if not exist "venv" (
    echo [!] Виртуальное окружение не найдено. Запуск автоустановщика...
    call install_windows.bat
)

call venv\Scripts\activate.bat

echo ==============================================================================
echo                   ЗАПУСК ВЕБ-ПАНЕЛИ AVITO MAX PARSER
echo ==============================================================================
echo.
echo [*] Сервер запускается на http://127.0.0.1:8000
echo [*] Для остановки нажмите Ctrl + C
echo.

python web_server.py
pause
