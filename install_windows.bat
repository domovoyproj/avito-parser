@echo off
chcp 65001 >nul
title Avito Max Parser — Windows Auto-Installer
color 0B

echo ==============================================================================
echo        AVITO MAX PARSER ^& AI DEAL SCORING ENGINE — WINDOWS INSTALLER
echo ==============================================================================
echo.

:: 1. Check Python
echo [*] Проверка наличия Python 3...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    color 0C
    echo [ERROR] Python не обнаружен в системе!
    echo Пожалуйста, установите Python 3.10 или выше с официального сайта: https://www.python.org/downloads/
    echo ОБЯЗАТЕЛЬНО отметьте галочку "Add Python to PATH" при установке.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%i in ('python --version 2^>^&1') do set PY_VER=%%i
echo [+] Обнаружен Python: %PY_VER%
echo.

:: 2. Create Virtual Environment
echo [*] Создание виртуального окружения (venv)...
if not exist "venv" (
    python -m venv venv
    if %errorlevel% neq 0 (
        color 0C
        echo [ERROR] Не удалось создать виртуальное окружение!
        pause
        exit /b 1
    )
    echo [+] Виртуальное окружение venv успешно создано.
) else (
    echo [+] Виртуальное окружение venv уже существует.
)
echo.

:: 3. Upgrade pip and install requirements
echo [*] Обновление pip и установка зависимостей...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt
if %errorlevel% neq 0 (
    color 0C
    echo [ERROR] Ошибка при установке Python-пакетов из requirements.txt!
    pause
    exit /b 1
)
echo [+] Все зависимости успешно установлены.
echo.

:: 4. Install Playwright Chromium
echo [*] Установка браузерного движка Playwright Chromium...
playwright install chromium
if %errorlevel% neq 0 (
    color 0E
    echo [WARN] Возникла заминка при установке Chromium. Повторная попытка с флагом --with-deps...
    python -m playwright install chromium
)
echo [+] Браузер Chromium готов к фоновому парсингу.
echo.

:: 5. Setup .env file
if not exist ".env" (
    echo [*] Создание файла конфигурации .env из .env.example...
    copy .env.example .env >nul
    echo [+] Файл .env создан. Вы можете отредактировать его в Блокноте.
) else (
    echo [+] Файл конфигурации .env уже существует.
)
echo.

:: 6. Setup directories
if not exist "data" mkdir data
if not exist "exports" mkdir exports
if not exist "logs" mkdir logs

color 0A
echo ==============================================================================
echo                    УСТАНОВКА УСПЕШНО ЗАВЕРШЕНА!
echo ==============================================================================
echo.
echo Для запуска Веб-панели используйте:  start_server.bat
echo Для запуска Telegram-бота:           start_telegram_bot.bat
echo Для одновременного запуска всего:     start_all.bat
echo.
echo Панель управления будет доступна по адресу: http://127.0.0.1:8000
echo Логин по умолчанию: admin
echo Пароль:             admin123
echo ==============================================================================
echo.
pause
