#!/usr/bin/env bash
# ==============================================================================
# AVITO MAX PARSER — SYSTEMD SERVICE SETUP (UBUNTU / DEBIAN 24/7 DAEMON)
# ==============================================================================

set -e

if [ "$EUID" -ne 0 ]; then
  echo "Ошибка: Данный скрипт должен быть запущен с правами sudo или root!"
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURRENT_USER="${SUDO_USER:-$USER}"

echo "=============================================================================="
echo "          НАСТРОЙКА СЛУЖБЫ SYSTEMD ДЛЯ AVITO MAX PARSER (24/7 РЕЖИМ)          "
echo "=============================================================================="
echo "Каталог проекта: $PROJECT_DIR"
echo "Пользователь:    $CURRENT_USER"
echo ""

# 1. Create Web Panel & Parser Engine Service
echo "[*] Создание службы /etc/systemd/system/avito-parser.service..."
cat <<EOF > /etc/systemd/system/avito-parser.service
[Unit]
Description=Avito Max Parser Web Service & Data Engine
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/venv/bin/python web_server.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
StandardOutput=append:$PROJECT_DIR/logs/systemd_web.log
StandardError=append:$PROJECT_DIR/logs/systemd_web_err.log

[Install]
WantedBy=multi-user.target
EOF

# 2. Create Telegram Bot Service
echo "[*] Создание службы /etc/systemd/system/avito-bot.service..."
cat <<EOF > /etc/systemd/system/avito-bot.service
[Unit]
Description=Avito Max Parser Telegram Bot Daemon
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/venv/bin/python telegram_bot.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
StandardOutput=append:$PROJECT_DIR/logs/systemd_bot.log
StandardError=append:$PROJECT_DIR/logs/systemd_bot_err.log

[Install]
WantedBy=multi-user.target
EOF

# 3. Reload systemd & Enable Services
echo "[*] Перезагрузка демона systemd и включение автозапуска..."
systemctl daemon-reload
systemctl enable avito-parser.service
systemctl enable avito-bot.service

# 4. Start Services
echo "[*] Запуск служб..."
systemctl restart avito-parser.service
systemctl restart avito-bot.service

echo ""
echo "=============================================================================="
echo "                     СЛУЖБЫ УСПЕШНО НАСТРОЕНЫ И ЗАПУЩЕНЫ!                     "
echo "=============================================================================="
echo ""
echo "Полезные команды для управления:"
echo "  Статус веб-панели:       sudo systemctl status avito-parser"
echo "  Статус Telegram-бота:    sudo systemctl status avito-bot"
echo "  Перезапуск веб-панели:   sudo systemctl restart avito-parser"
echo "  Перезапуск Telegram-бота:sudo systemctl restart avito-bot"
echo "  Просмотр живых логов:    sudo journalctl -u avito-parser -f"
echo "=============================================================================="
