@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Avito Max Parser — Windows Auto-Installer
color 0B

echo ==============================================================================
echo        AVITO MAX PARSER ^& AI DEAL SCORING ENGINE — WINDOWS INSTALLER
echo ==============================================================================
echo.

rem 1. Check Python
echo [*] Проверка наличия Python 3...
where py.exe >nul 2>&1
if %errorlevel% equ 0 (
    set "SYS_PYTHON=py -3"
) else (
    where python.exe >nul 2>&1
    if %errorlevel% equ 0 (
        set "SYS_PYTHON=python"
    ) else (
        color 0C
        echo [ERROR] Python не обнаружен в системе!
        echo Пожалуйста, установите Python 3.10+ и отметьте "Add Python to PATH".
        pause
        exit /b 1
    )
)

rem 2. Create Virtual Environment (.venv)
echo [*] Создание виртуального окружения (.venv)...
if not exist ".venv\Scripts\python.exe" (
    %SYS_PYTHON% -m venv .venv
    if %errorlevel% neq 0 (
        color 0C
        echo [ERROR] Не удалось создать виртуальное окружение .venv!
        pause
        exit /b 1
    )
    echo [+] Виртуальное окружение .venv успешно создано.
) else (
    echo [+] Виртуальное окружение .venv уже существует.
)
echo.

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "VENV_PIP=%~dp0.venv\Scripts\pip.exe"

rem 3. Upgrade pip and install requirements
echo [*] Обновление pip и установка зависимостей...
"%VENV_PY%" -m pip install --upgrade pip --quiet
"%VENV_PY%" -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    color 0C
    echo [ERROR] Ошибка при установке Python-пакетов из requirements.txt!
    pause
    exit /b 1
)
echo [+] Все зависимости успешно установлены.
echo.
rem 4. Install Playwright Chromium
echo [*] Установка браузерного движка Playwright Chromium...
"%VENV_PY%" -m playwright install chromium
if %errorlevel% neq 0 (
    color 0E
    echo [WARN] Повторная попытка playwright install с флагом --with-deps...
    "%VENV_PY%" -m playwright install --with-deps chromium
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
