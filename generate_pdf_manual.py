#!/usr/bin/env python3
"""
Генератор премиального PDF-руководства пользователя и администратора для Avito Max Parser.
Использует Playwright Chromium для рендеринга идеального PDF-документа.
"""

import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

OUTPUT_PDF = Path(__file__).parent / "Documentation_Avito_Max_Parser.pdf"

HTML_CONTENT = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Avito Max Parser — Полное Руководство Пользователя и Администратора</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap');

        @page {
            size: A4;
            margin: 20mm 15mm 20mm 15mm;
            @bottom-right {
                content: counter(page);
                font-family: 'Inter', sans-serif;
                font-size: 9pt;
                color: #888;
            }
            @bottom-left {
                content: "Avito Max Parser — Enterprise Edition";
                font-family: 'Inter', sans-serif;
                font-size: 9pt;
                color: #888;
            }
        }

        * {
            box-sizing: border-box;
        }

        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            color: #1a1a1a;
            line-height: 1.6;
            font-size: 10pt;
            background: #ffffff;
            margin: 0;
            padding: 0;
        }

        /* --- COVER PAGE --- */
        .cover-page {
            page-break-after: always;
            height: 100vh;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            background: #050505;
            color: #ffffff;
            margin: -20mm -15mm -20mm -15mm;
            padding: 40mm 25mm 25mm 25mm;
            box-sizing: border-box;
        }

        .cover-badge {
            display: inline-block;
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
            color: #ffffff;
            font-family: 'JetBrains Mono', monospace;
            font-size: 9pt;
            font-weight: 600;
            padding: 6px 14px;
            border-radius: 6px;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 25px;
        }

        .cover-title {
            font-size: 34pt;
            font-weight: 800;
            line-height: 1.1;
            letter-spacing: -1.5px;
            margin: 0 0 15px 0;
            color: #ffffff;
        }

        .cover-title span {
            color: #f59e0b;
        }

        .cover-subtitle {
            font-size: 14pt;
            font-weight: 400;
            color: #a1a1aa;
            line-height: 1.5;
            max-width: 600px;
            margin-bottom: 40px;
        }

        .cover-features {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 15px;
            margin-top: 20px;
        }

        .cover-feat-item {
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 8px;
            padding: 12px 16px;
        }

        .cover-feat-item strong {
            display: block;
            font-size: 11pt;
            color: #ffffff;
            margin-bottom: 4px;
        }

        .cover-feat-item span {
            font-size: 9pt;
            color: #888888;
        }

        .cover-footer {
            border-top: 1px solid rgba(255, 255, 255, 0.1);
            padding-top: 20px;
            display: flex;
            justify-content: space-between;
            font-size: 9pt;
            color: #71717a;
            font-family: 'JetBrains Mono', monospace;
        }

        /* --- CONTENT STYLING --- */
        h1 {
            font-size: 20pt;
            font-weight: 800;
            color: #050505;
            border-bottom: 2px solid #050505;
            padding-bottom: 8px;
            margin-top: 35px;
            margin-bottom: 15px;
            letter-spacing: -0.5px;
            page-break-after: avoid;
        }

        h2 {
            font-size: 14pt;
            font-weight: 700;
            color: #18181b;
            margin-top: 25px;
            margin-bottom: 10px;
            letter-spacing: -0.3px;
            page-break-after: avoid;
        }

        h3 {
            font-size: 11pt;
            font-weight: 600;
            color: #27272a;
            margin-top: 18px;
            margin-bottom: 6px;
            page-break-after: avoid;
        }

        p {
            margin: 0 0 10px 0;
            color: #3f3f46;
        }

        ul, ol {
            margin: 0 0 12px 0;
            padding-left: 20px;
            color: #3f3f46;
        }

        li {
            margin-bottom: 4px;
        }

        code {
            font-family: 'JetBrains Mono', monospace;
            font-size: 8.5pt;
            background: #f4f4f5;
            border: 1px solid #e4e4e7;
            padding: 2px 5px;
            border-radius: 4px;
            color: #09090b;
        }

        pre {
            background: #09090b;
            color: #f4f4f5;
            padding: 12px 16px;
            border-radius: 8px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 8.5pt;
            line-height: 1.45;
            overflow-x: auto;
            margin: 10px 0 15px 0;
            page-break-inside: avoid;
        }

        /* --- TABLES --- */
        table {
            width: 100%;
            border-collapse: collapse;
            margin: 15px 0 20px 0;
            font-size: 9pt;
            page-break-inside: avoid;
        }

        th {
            background: #09090b;
            color: #ffffff;
            text-align: left;
            padding: 8px 12px;
            font-weight: 600;
            border: 1px solid #09090b;
        }

        td {
            padding: 8px 12px;
            border: 1px solid #e4e4e7;
            color: #27272a;
        }

        tr:nth-child(even) td {
            background: #fafafa;
        }

        /* --- CALLOUT BOXES --- */
        .callout {
            border-radius: 8px;
            padding: 12px 16px;
            margin: 15px 0;
            font-size: 9pt;
            page-break-inside: avoid;
        }

        .callout-info {
            background: #f0fdf4;
            border-left: 4px solid #10b981;
            color: #065f46;
        }

        .callout-warn {
            background: #fffbeb;
            border-left: 4px solid #f59e0b;
            color: #92400e;
        }

        .callout-dark {
            background: #18181b;
            border-left: 4px solid #ffffff;
            color: #f4f4f5;
        }

        .page-break {
            page-break-after: always;
        }
    </style>
</head>
<body>

    <!-- ===================================================================== -->
    <!-- 1. COVER PAGE -->
    <!-- ===================================================================== -->
    <div class="cover-page">
        <div>
            <div class="cover-badge">Enterprise Edition • v2.5.0</div>
            <h1 class="cover-title">AVITO MAX <span>PARSER</span></h1>
            <div class="cover-subtitle">
                Профессиональная система мониторинга, парсинга и нейросетевой оценки выгодности сделок Авито в реальном времени.
            </div>

            <div class="cover-features">
                <div class="cover-feat-item">
                    <strong>💎 AI Deal Scoring Engine</strong>
                    <span>Многофакторный математический и LLM анализ маржинальности лотов.</span>
                </div>
                <div class="cover-feat-item">
                    <strong>⚡ Двухъядерный скрапер</strong>
                    <span>curl_cffi TLS-эмуляция + Playwright Chromium Anti-Detect.</span>
                </div>
                <div class="cover-feat-item">
                    <strong>📡 24/7 Telegram Мониторинг</strong>
                    <span>Мгновенные карточки с фото и фильтрацией по минимальному скорингу.</span>
                </div>
                <div class="cover-feat-item">
                    <strong>🖥️ Linear-Style Web Terminal</strong>
                    <span>Панель управления на FastAPI с WebSockets и экспортом Excel/HTML.</span>
                </div>
            </div>
        </div>

        <div class="cover-footer">
            <div>ОФИЦИАЛЬНОЕ РУКОВОДСТВО ПОЛЬЗОВАТЕЛЯ И АДМИНИСТРАТОРА</div>
            <div>2026 ГОД</div>
        </div>
    </div>

    <!-- ===================================================================== -->
    <!-- 2. EXECUTIVE OVERVIEW -->
    <!-- ===================================================================== -->
    <h1>1. Описание системы и архитектура</h1>
    <p>
        <strong>Avito Max Parser</strong> представляет собой автономный высокопроизводительный комплекс для автоматизации сбора, фильтрации и глубокого анализа объявлений на платформе Avito.ru. Продукт разработан с акцентом на стабильность работы в режиме 24/7, защиту от детекта и объективную оценку инвестиционной привлекательности каждого найденного товара.
    </p>

    <h2>1.1. Архитектура модулей</h2>
    <ul>
        <li><strong>Scraper Core (Движок сбора данных):</strong> Сочетает сверхбыстрый асинхронный HTTP-клиент с эмуляцией TLS браузера и управляемый Headless Chromium для рендеринга страниц с динамическим контентом.</li>
        <li><strong>AI Deal Scoring Engine:</strong> Двухуровневый алгоритм оценки: математический многофакторный расчет отклонения от рыночной медианы плюс опциональный экспертный анализ через нейросети (DeepSeek / OpenAI / Ollama).</li>
        <li><strong>Storage Layer:</strong> Асинхронная база данных SQLite с оптимизированными индексами, сохраняющая историю цен, продавцов, параметры и кэш дубликатов.</li>
        <li><strong>Web Control Panel:</strong> Веб-интерфейс на FastAPI с темным дизайном в стиле Linear/Vercel, живыми логами через WebSockets и поддержкой пакетных операций.</li>
        <li><strong>Telegram Bot Daemon:</strong> Асинхронный бот для оперативной доставки уведомлений о лотах с гибкими фильтрами по скидке и оценке сделки.</li>
    </ul>

    <div class="callout callout-info">
        <strong>Ключевое преимущество:</strong> Система полностью автономна и не требует оплаты сторонних облачных сервисов парсинга. Все данные хранятся локально на вашем сервере.
    </div>

    <!-- ===================================================================== -->
    <!-- 3. SYSTEM REQUIREMENTS -->
    <!-- ===================================================================== -->
    <div class="page-break"></div>
    <h1>2. Системные требования</h1>

    <table>
        <thead>
            <tr>
                <th>Параметр</th>
                <th>Минимальные требования</th>
                <th>Рекомендуемые требования</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td><strong>Операционная система</strong></td>
                <td>Ubuntu 20.04+ / Debian 11+ / Windows 10/11 / Windows Server 2019+</td>
                <td>Ubuntu 22.04 / 24.04 LTS или Windows Server 2022</td>
            </tr>
            <tr>
                <td><strong>Процессор (CPU)</strong></td>
                <td>1 vCPU / 2.0 GHz</td>
                <td>2+ vCPU (для параллельного мониторинга 10+ задач)</td>
            </tr>
            <tr>
                <td><strong>Оперативная память (RAM)</strong></td>
                <td>1 GB RAM (со SWAP)</td>
                <td>2 GB – 4 GB RAM</td>
            </tr>
            <tr>
                <td><strong>Дисковое пространство</strong></td>
                <td>3 GB SSD</td>
                <td>10+ GB SSD (для долгосрочного хранения базы и дампов)</td>
            </tr>
            <tr>
                <td><strong>Версия Python</strong></td>
                <td>Python 3.10.x</td>
                <td>Python 3.11.x или 3.12.x</td>
            </tr>
        </tbody>
    </table>

    <!-- ===================================================================== -->
    <!-- 4. INSTALLATION GUIDE: WINDOWS -->
    <!-- ===================================================================== -->
    <h1>3. Установка и запуск на Windows Server / Desktop</h1>

    <p>Для ОС Windows подготовлен автоматический установщик, выполняющий полную настройку окружения за один шаг.</p>

    <h3>Шаг 1. Подготовка Python</h3>
    <ol>
        <li>Скачайте инсталлятор Python 3.10+ с официального сайта <code>https://www.python.org/downloads/</code>.</li>
        <li><strong>Критически важно:</strong> При установке обязательно установите флажок <code>[x] Add Python to PATH</code>.</li>
    </ol>

    <h3>Шаг 2. Автоматическая установка</h3>
    <ol>
        <li>Распакуйте релизный архив проекта в рабочую папку (например, <code>C:\\avito_parser</code>).</li>
        <li>Запустите файл <strong><code>install_windows.bat</code></strong> от имени пользователя или администратора.</li>
        <li>Скрипт автоматически создаст виртуальное окружение <code>venv</code>, установит библиотеки и скачает браузер Chromium.</li>
    </ol>

    <h3>Шаг 3. Запуск сервисов</h3>
    <ul>
        <li><strong><code>start_all.bat</code></strong> — Запуск Веб-панели и Telegram-бота одновременно в отдельных консольных окнах.</li>
        <li><strong><code>start_server.bat</code></strong> — Запуск только Веб-интерфейса (порт 8000).</li>
        <li><strong><code>start_telegram_bot.bat</code></strong> — Запуск только демона Telegram-бота.</li>
    </ul>

    <div class="callout callout-dark">
        <strong>Доступ к панели управления:</strong><br>
        URL: <code>http://127.0.0.1:8000</code><br>
        Логин по умолчанию: <code>admin</code><br>
        Пароль по умолчанию: <code>admin123</code>
    </div>

    <!-- ===================================================================== -->
    <!-- 5. INSTALLATION GUIDE: UBUNTU LINUX -->
    <!-- ===================================================================== -->
    <div class="page-break"></div>
    <h1>4. Установка и запуск на Ubuntu / Debian Linux Server</h1>

    <p>Для работы на сервере без графической оболочки (VPS/VDS) реализована полная автоматизация установки и управления через <code>systemd</code>.</p>

    <h3>Шаг 1. Клонирование и автоустановка</h3>
    <pre># Перейдите в каталог установки
cd /opt
git clone &lt;ВАШ_РЕПОЗИТОРИЙ&gt; avito_parser
cd avito_parser

# Запустите скрипт установки системных пакетов и Chromium
chmod +x install_ubuntu.sh setup_systemd.sh
sudo ./install_ubuntu.sh</pre>

    <h3>Шаг 2. Настройка фоновой службы 24/7 (Systemd)</h3>
    <p>Чтобы приложение автоматически стартовало при перезагрузке сервера и мгновенно восстанавливалось при сбоях, выполните:</p>
    <pre>sudo ./setup_systemd.sh</pre>

    <h3>Управление службами в консоли Linux:</h3>
    <table>
        <thead>
            <tr>
                <th>Действие</th>
                <th>Команда</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td>Статус веб-сервера</td>
                <td><code>sudo systemctl status avito-parser</code></td>
            </tr>
            <tr>
                <td>Статус Telegram-бота</td>
                <td><code>sudo systemctl status avito-bot</code></td>
            </tr>
            <tr>
                <td>Перезапуск веб-сервера</td>
                <td><code>sudo systemctl restart avito-parser</code></td>
            </tr>
            <tr>
                <td>Просмотр живых логов</td>
                <td><code>sudo journalctl -u avito-parser -f</code></td>
            </tr>
        </tbody>
    </table>

    <!-- ===================================================================== -->
    <!-- 6. DOCKER DEPLOYMENT -->
    <!-- ===================================================================== -->
    <h2>4.1. Развертывание через Docker Compose</h2>
    <p>Для контейнеризированной изоляции используйте готовый <code>docker-compose.yml</code>:</p>
    <pre># Настройте конфигурацию
cp .env.example .env
nano .env

# Запустите контейнеры в фоновом режиме
docker compose up -d --build

# Просмотр логов
docker compose logs -f</pre>

    <!-- ===================================================================== -->
    <!-- 7. AI DEAL SCORING FORMULA -->
    <!-- ===================================================================== -->
    <div class="page-break"></div>
    <h1>5. Алгоритм оценки сделки (AI Deal Scoring Engine)</h1>

    <p>
        Скоринг сделки вычисляется по шкале от <strong>0 до 100 баллов</strong> на основе объективных рыночных и контентных критериев.
    </p>

    <table>
        <thead>
            <tr>
                <th>Категория оценки</th>
                <th>Макс. балл</th>
                <th>Правило начисления</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td><strong>Ценовой фактор (Медиана рынка)</strong></td>
                <td><strong>45</strong></td>
                <td>
                    ≥ 30% ниже медианы: <strong>+45</strong><br>
                    ≥ 20% ниже медианы: <strong>+35</strong><br>
                    ≥ 10% ниже медианы: <strong>+25</strong><br>
                    В пределах нормы: <strong>+15</strong>
                </td>
            </tr>
            <tr>
                <td><strong>Надежность продавца</strong></td>
                <td><strong>20</strong></td>
                <td>
                    Верификация документов/Госуслуг: <strong>+7</strong><br>
                    Рейтинг ≥ 4.8★ при наличии отзывов: <strong>+8</strong> (4.5–4.7: +4)<br>
                    Число отзывов > 10: <strong>+5</strong>
                </td>
            </tr>
            <tr>
                <td><strong>Качество объявления</strong></td>
                <td><strong>15</strong></td>
                <td>
                    Фотогалерея ≥ 4 фото: <strong>+6</strong><br>
                    Структурированные характеристики: <strong>+5</strong><br>
                    Подробное описание (> 120 симв.): <strong>+4</strong>
                </td>
            </tr>
            <tr>
                <td><strong>Безопасность сделки</strong></td>
                <td><strong>10</strong></td>
                <td>
                    Доступна Авито Доставка: <strong>+7</strong><br>
                    Указано метро или точный адрес: <strong>+3</strong>
                </td>
            </tr>
            <tr>
                <td><strong>Динамика цены</strong></td>
                <td><strong>10</strong></td>
                <td>Зафиксировано снижение цены продавцом (old_price > price): <strong>+10</strong></td>
            </tr>
            <tr>
                <td><strong>Штрафы за дефекты</strong></td>
                <td><strong>-30</strong></td>
                <td>Обнаружение стоп-слов (*«на запчасти», «разбит», «не включается», «копия»*): <strong>-25 баллов</strong>.</td>
            </tr>
        </tbody>
    </table>

    <h2>5.1. Градация лотов (Deal Grades)</h2>
    <ul>
        <li><strong>💎 GEM (85–100 баллов):</strong> Уникальное сверхвыгодное предложение со скидкой от 30% и надежным продавцом.</li>
        <li><strong>🔥 HOT DEAL (70–84 балла):</strong> Отличная цена ниже рынка (15–25%), высокая ликвидность.</li>
        <li><strong>⚖️ FAIR (50–69 баллов):</strong> Стандартная среднерыночная цена.</li>
        <li><strong>⚠️ CAUTION (&lt; 50 баллов):</strong> Завышенная цена, риск дефекта или подозрительный продавец.</li>
    </ul>

    <!-- ===================================================================== -->
    <!-- 8. CONFIGURATION REFERENCE -->
    <!-- ===================================================================== -->
    <div class="page-break"></div>
    <h1>6. Справочник конфигурации (.env)</h1>

    <table>
        <thead>
            <tr>
                <th>Переменная</th>
                <th>Значение по умолчанию</th>
                <th>Назначение</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td><code>WEB_HOST</code></td>
                <td><code>0.0.0.0</code></td>
                <td>IP-адрес для привязки веб-панели (0.0.0.0 для всех интерфейсов).</td>
            </tr>
            <tr>
                <td><code>WEB_PORT</code></td>
                <td><code>8000</code></td>
                <td>Сетевой порт веб-интерфейса.</td>
            </tr>
            <tr>
                <td><code>TELEGRAM_BOT_TOKEN</code></td>
                <td>—</td>
                <td>API-токен Telegram-бота, полученный у @BotFather.</td>
            </tr>
            <tr>
                <td><code>TELEGRAM_ADMIN_IDS</code></td>
                <td>—</td>
                <td>Telegram ID администраторов через запятую (для авторизации).</td>
            </tr>
            <tr>
                <td><code>SCRAPER_HEADLESS</code></td>
                <td><code>true</code></td>
                <td><code>true</code> для фоновой работы на серверах; <code>false</code> для открытия видимого окна.</td>
            </tr>
            <tr>
                <td><code>SCRAPER_PAGE_DELAY_MIN</code></td>
                <td><code>2.0</code></td>
                <td>Минимальная задержка между переходами по страницам (сек).</td>
            </tr>
            <tr>
                <td><code>SCRAPER_PAGE_DELAY_MAX</code></td>
                <td><code>5.0</code></td>
                <td>Максимальная задержка между переходами (рандомизация анти-бан).</td>
            </tr>
            <tr>
                <td><code>AI_PROVIDER</code></td>
                <td><code>deepseek</code></td>
                <td>Провайдер LLM: <code>deepseek</code>, <code>openrouter</code>, <code>openai</code>, <code>ollama</code>.</td>
            </tr>
            <tr>
                <td><code>AI_API_KEY</code></td>
                <td>—</td>
                <td>API-ключ нейросети для генерации экспресс-резюме.</td>
            </tr>
        </tbody>
    </table>

    <!-- ===================================================================== -->
    <!-- 9. PRODUCTION & MAINTENANCE -->
    <!-- ===================================================================== -->
    <h1>7. Обслуживание, бэкапы и безопасность</h1>

    <h2>7.1. Резервное копирование базы данных</h2>
    <p>Вся информация (каталог товаров, история цен, задачи мониторинга) хранится в файле <code>data/avito.db</code>. Для создания резервной копии достаточно скопировать этот файл:</p>
    <pre>cp /opt/avito_parser/data/avito.db /opt/backups/avito_$(date +%Y%m%d).db</pre>

    <h2>7.2. Ротация логов</h2>
    <p>Логи системы записываются в кольцевой буфер и файл <code>logs/systemd_web.log</code>. Для очистки старых записей используйте команду очистки базы через веб-интерфейс или ротацию logrotate.</p>

    <div class="callout callout-warn">
        <strong>Рекомендация по безопасности:</strong> Обязательно смените пароль администратора по умолчанию (<code>admin123</code>) в разделе Настройки панели управления после первого входа!
    </div>

    <br><br>
    <div style="text-align: center; font-size: 8pt; color: #a1a1aa; font-family: 'JetBrains Mono', monospace;">
        © 2026 Avito Max Parser Enterprise Edition. Все права защищены.
    </div>

</body>
</html>
"""

async def generate_pdf():
    print("[*] Запуск генератора PDF через Playwright Chromium...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Load HTML
        await page.set_content(HTML_CONTENT, wait_until="networkidle")
        
        # Render PDF with print background and proper margins
        await page.pdf(
            path=str(OUTPUT_PDF),
            format="A4",
            print_background=True,
            margin={
                "top": "0mm",
                "bottom": "0mm",
                "left": "0mm",
                "right": "0mm"
            }
        )
        await browser.close()
        
    print(f"[+] Премиальное PDF-руководство успешно создано: {OUTPUT_PDF} ({OUTPUT_PDF.stat().st_size / 1024:.1f} KB)")

if __name__ == "__main__":
    asyncio.run(generate_pdf())
