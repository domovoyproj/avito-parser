@echo off
chcp 65001 >nul
title Avito Max Parser — Full Launcher
color 0E

echo ==============================================================================
echo               ЗАПУСК ВСЕХ КОМПОНЕНТОВ AVITO MAX PARSER
echo ==============================================================================
echo.
echo 1. Запуск Веб-сервера (FastAPI / Uvicorn на порту 8000)...
start "Avito Max Parser — Web Server" cmd /k start_server.bat

timeout /t 2 /nobreak >nul

echo 2. Запуск Telegram-бота (Мониторинг и уведомления)...
start "Avito Max Parser — Telegram Bot" cmd /k start_telegram_bot.bat

echo.
echo [+] Все сервисы успешно запущены в фоновых окнах!
echo Панель управления доступна по адресу: http://127.0.0.1:8000
echo.
timeout /t 5 >nul
