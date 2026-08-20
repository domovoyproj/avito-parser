#!/usr/bin/env bash
# ==============================================================================
# AVITO MAX PARSER — UBUNTU / DEBIAN LINUX SERVER AUTO-INSTALLER
# ==============================================================================

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}==============================================================================${NC}"
echo -e "${BLUE}        AVITO MAX PARSER & AI DEAL SCORING — UBUNTU AUTO-INSTALLER           ${NC}"
echo -e "${BLUE}==============================================================================${NC}"
echo ""

# 1. Check Root / Sudo
if [ "$EUID" -ne 0 ]; then
  echo -e "${YELLOW}[!] Рекомендуется запускать с правами sudo или под root для установки системных пакетов.${NC}"
fi

# 2. Update Apt & Install System Packages
echo -e "${BLUE}[*] Обновление репозиториев apt и установка системных библиотек...${NC}"
sudo apt update -y
sudo apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    curl \
    git \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    fonts-liberation

echo -e "${GREEN}[+] Системные пакеты успешно установлены.${NC}"
echo ""

# 3. Create Virtual Environment
echo -e "${BLUE}[*] Создание виртуального окружения (venv)...${NC}"
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo -e "${GREEN}[+] Виртуальное окружение venv создано.${NC}"
else
    echo -e "${GREEN}[+] Виртуальное окружение venv уже существует.${NC}"
fi
echo ""

# 4. Activate venv & Install Python Packages
echo -e "${BLUE}[*] Установка зависимостей Python из requirements.txt...${NC}"
source venv/bin/activate
pip install --upgrade pip --quiet
pip install -r requirements.txt
echo -e "${GREEN}[+] Все Python-пакеты успешно установлены.${NC}"
echo ""

# 5. Install Playwright Chromium with dependencies
echo -e "${BLUE}[*] Установка браузерного движка Playwright Chromium для Linux...${NC}"
python -m playwright install --with-deps chromium
echo -e "${GREEN}[+] Движок Chromium успешно настроен для фонового парсинга.${NC}"
echo ""

# 6. Setup Configuration
if [ ! -f ".env" ]; then
    echo -e "${BLUE}[*] Создание файла конфигурации .env из .env.example...${NC}"
    cp .env.example .env
    # Ensure headless is true on Linux server
    sed -i 's/SCRAPER_HEADLESS=false/SCRAPER_HEADLESS=true/g' .env
    echo -e "${GREEN}[+] Файл .env создан (SCRAPER_HEADLESS установлен в true).${NC}"
else
    echo -e "${GREEN}[+] Файл .env уже существует.${NC}"
fi

# 7. Create required directories
mkdir -p data exports logs
chmod -R 755 data exports logs

echo ""
echo -e "${GREEN}==============================================================================${NC}"
echo -e "${GREEN}                    УСТАНОВКА НА UBUNTU УСПЕШНО ЗАВЕРШЕНА!                    ${NC}"
echo -e "${GREEN}==============================================================================${NC}"
echo ""
echo -e "Для ручного запуска сервера:"
echo -e "  ${YELLOW}source venv/bin/activate && python web_server.py${NC}"
echo ""
echo -e "Для настройки автоматической службы 24/7 (systemd):"
echo -e "  ${YELLOW}sudo ./setup_systemd.sh${NC}"
echo ""
echo -e "Панель управления будет доступна на порту 8000: ${BLUE}http://YOUR_SERVER_IP:8000${NC}"
echo -e "Логин по умолчанию: ${YELLOW}admin${NC} / Пароль: ${YELLOW}admin123${NC}"
echo -e "${GREEN}==============================================================================${NC}"
