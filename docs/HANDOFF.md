# Передача работы другой модели

## Обновление 2.6.0 — 2026-10-12

Ветка feat/reseller-workspace-2.6: полноценный визуальный редизайн и управляемая проверка CAPTCHA. Прототип до реализации — docs/design-prototype.html. Палитра: светлый тёплый фон, графитово-зелёная навигация, evergreen/lime акценты; без удалённых шрифтов, вымышленных фотографий и mock-метрик в production. Все экраны используют design-system.css/workspace.css; styles.css — небольшой слой совместимости.

Приоритеты продукта: Обзор → Находки → Поиски → Мои списки/Снижения цены. Служебные экраны — в «Дополнительно». Каталог: цена, оценка, причина, действие; карточки/таблица; частые фильтры на виду, личные фильтры и обслуживание свёрнуты. В карточке — расчёт перепродажи по введённым ценам/расходам, не прогноз продажи. Новый сбор принимает название либо ссылку, имеет реальные cancel/status/recovery.

access_guard.py отличает страницу проверки от CAPTCHA SDK. Browser engine ожидает ручную проверку до 5 минут, не подтверждает её по одному исчезнувшему заголовку, прекращает после третьего challenge. Результаты браузерного/HTTP сбора сохраняются по страницам. parser jobs различают completed/blocked/error/cancelled и outcome; тайм-аут зависит от глубины. Новые GET snapshot/attention и POST retry-human/dismiss добавлены без удаления прежних endpoint-ов/WS. Мониторинг после блокировок: 5/15/60 минут паузы, общий заметный баннер, снятие предупреждения после успешного ручного сбора. Задачи и circuit breaker в памяти процесса: restart сбрасывает их, но не каталог/SQLite историю.

Видимый Chromium работает на компьютере сервера. Linux без DISPLAY и заблокированный рабочий стол не подходят. Авто-решатель и внешние CAPTCHA-сервисы не подключены. TUN-VPN не отключался, маршруты не менялись; требуется доступный маршрут Авито или отдельный рабочий прокси. Live Avito с текущим VPN не подтверждён. Автоповтор блокировки не нужен.

Проверки на временных БД: run_offline_tests.py — 77 unit tests и 3 regression scripts; test_parser_jobs — 9 специальных сценариев; Ruff E9/F63/F7/F82; pip-audit constraints — 0; npm audit --omit=dev — 0. Расширенный Chromium smoke: 13 экранов ×360/768/1440, theme persistence, login 429 countdown, menu/focus/Escape, карточки/таблица, фильтры, feedback/watchlist/export, калькулятор, error/retry, blocked/manual/cancelled/reload, XSS и роли. Screenshots before/after — ui-artifacts/ (не коммитить), CI artifact ui-screenshots. Перед публикацией проверить свежий CI, не подменять его локальными результатами.

Полный npm audit имеет 5 high advisory paths в dev-зависимостях Tailwind 3/braces (GHSA-vfj7-8cjw-p6xm, для braces 3.0.3 пока нет исправленной версии). Они не входят в runtime/release; build работает с доверенными шаблонами. PostCSS selector parser обновлён override 7.1.6, moderate advisory устранён. Следующая отдельная задача — миграция Tailwind toolchain с проверкой воспроизводимого CSS.

VERSION в version.py — единая версия API/UI/архивов. Windows stdout/stderr настроены на UTF-8; архив включает новые модули и start_panel/start_web. Схема БД по-прежнему 10, авторизация не менялась. Не закрывать #10/#13 до live-интеграций; следующий шаг — безопасная проверка маршрута Авито и ручная CAPTCHA на реальном источнике, затем Telegram/webhook/LLM.

## Предыдущий этап

Пользователь поручил весь roadmap, включая полноценное обновление UI. Базовая реализация [PR #14](https://github.com/domovoyproj/avito-parser/pull/14) уже в `main`. Затем отдельными PR выполнены четыре дополнительные Issue: [#17 → PR #19](https://github.com/domovoyproj/avito-parser/pull/19) (история мониторинга), [#15 → PR #20](https://github.com/domovoyproj/avito-parser/pull/20) (личные списки и адресные алерты), [#16 → PR #21](https://github.com/domovoyproj/avito-parser/pull/21) (обратная связь о скоринге), [#18 → PR #22](https://github.com/domovoyproj/avito-parser/pull/22) (FTS5-поиск и сохранённые фильтры). Все четыре влиты и прошли CI. Актуальный статус: [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

## Архитектура

- `web_server.py`: lifespan, middleware, страницы, health/ready/metrics и регистрация роутеров.
- `routers/`: auth, settings, searches, items, analytics, monitoring, parser, exports. `dependencies.py`: FastAPI overrides для БД, engines, jobs и monitoring. Middleware проверяет настоящую БД сессий независимо от подмены бизнес-зависимости в тесте.
- `database.py`: соединения, атомарные миграции, backup и совместимый фасад. SQL в `repositories/`, включая `monitoring_runs`, `watchlists` и `feedback`. `SCHEMA_VERSION` — единый номер схемы; future version отклоняется до DDL. Версия 10 добавляет FTS5-индекс и личные фильтры.
- `monitoring.py`: общий web/bot/CLI сервис. `coordination.py`: renewable SQLite leases с отменой при потере владельца. `parser_jobs.py`: limits, timeout/cancel/shutdown.
- `outbox.py`/`notifications.py`: события per recipient, leases/retry/quiet hours/filters и общий formatter. Доставка at-least-once.
- `llm_store.py`: общий SQLite cache и daily budget. `export_jobs.py`/`streaming_export.py`: durable очередь, snapshot чтения, batches 1000, CSV/write-only XLSX. `manage_db.py`: backup/restore CLI.
- UI: Jinja2/JS, локальные vendor assets, npm Tailwind build. Tokens в styles.css, dashboard в overview.js, темы/keyboard dialogs в theme.js.
- `catalog_filters.py`: общий параметризованный фильтр для каталога и обоих экспортов. Обычные слова ищутся по FTS5 `unicode61` с префиксами; знаки пунктуации — буквальным `LIKE`. Личные фильтры — `routers/saved_filters.py`. `/feedback` показывает агрегаты, `tools/evaluate_feedback.py` даёт обезличенный offline-набор. `tools/benchmark_search.py` воспроизводит замер 100k.

## Команды

```sh
python -m pip install -r requirements-dev.txt -c constraints.txt
python run_offline_tests.py
python -m ruff check --select E9,F63,F7,F82 .
python -m pip_audit -r constraints.txt
npm ci
npm run build
python -m playwright install chromium
# Отдельный терминал:
python tools/ui_fixture.py
python tools/ui_smoke.py --out ui-artifacts
```

Тесты используют временные БД/mock внешние сервисы. Fixture server: 127.0.0.1:18765; его пароли относятся только к временной базе. Не использовать для установки.

Перед продолжением читать AGENTS.md, статус и выбранную Issue. Graphify (`graphifyy`) установлен отдельным CLI: query/affected/god-nodes/explain перед анализом; `graphify extract . --code-only` и `graphify export html` после структурных изменений. graphify-out/ игнорируется. AST не покрывает template/runtime wiring; vendor JS может стать шумным hub. SQL fixture требует tree_sitter_sql для индексации — сама миграция проверена SQLite-тестом.

CI: Windows/Linux Python 3.10/3.11/3.13, audit, импорт распакованного релиза, Docker UID/readiness/restart, отдельный Chromium UI job с screenshots artifact. Не объявлять CI зелёным до результата. Эпики закрывать после критериев и слияния.

Следующий шаг: выполнить backup и smoke установки по OPERATIONS.md на отдельной тестовой копии реальной БД, измерить FTS на настоящем каталоге и проверить live Avito/Telegram/webhook/LLM. Только после этого закрывать соответствующие Issues. Confidence — полнота данных, не вероятность выгодной покупки. Архивы содержат новые modules/constraints; установка требует доступности package registries.
