# Передача работы другой модели

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
