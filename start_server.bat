@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Avito Max Parser — Web Control Terminal
color 0A

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
echo                   ЗАПУСК ВЕБ-ПАНЕЛИ AVITO MAX PARSER
echo ==============================================================================
echo.
echo [*] Сервер запускается на http://127.0.0.1:8000
echo [*] Для остановки нажмите Ctrl + C
echo.

"%PY_BIN%" web_server.py
pause
