# Передача работы другой модели

Пользователь поручил весь roadmap, включая полноценное обновление UI. [Issue #13](https://github.com/domovoyproj/avito-parser/issues/13), [PR #14](https://github.com/domovoyproj/avito-parser/pull/14), ветка `codex/upgrade-foundation`, исходный main `6baf52b`. Слияние и развёртывание не выполнены. Актуальный статус: [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md). Старые комментарии описывают промежуточные результаты.

## Архитектура

- `web_server.py`: lifespan, middleware, страницы, health/ready/metrics и регистрация роутеров.
- `routers/`: auth, settings, searches, items, analytics, monitoring, parser, exports. `dependencies.py`: FastAPI overrides для БД, engines, jobs и monitoring. Middleware проверяет настоящую БД сессий независимо от подмены бизнес-зависимости в тесте.
- `database.py`: соединения, атомарные миграции, backup и совместимый фасад. SQL в `repositories/{items,searches,users,settings,analytics}.py`. `SCHEMA_VERSION` — единый номер схемы; future version отклоняется до DDL.
- `monitoring.py`: общий web/bot/CLI сервис. `coordination.py`: renewable SQLite leases с отменой при потере владельца. `parser_jobs.py`: limits, timeout/cancel/shutdown.
- `outbox.py`/`notifications.py`: события per recipient, leases/retry/quiet hours/filters и общий formatter. Доставка at-least-once.
- `llm_store.py`: общий SQLite cache и daily budget. `export_jobs.py`/`streaming_export.py`: durable очередь, snapshot чтения, batches 1000, CSV/write-only XLSX. `manage_db.py`: backup/restore CLI.
- UI: Jinja2/JS, локальные vendor assets, npm Tailwind build. Tokens в styles.css, dashboard в overview.js, темы/keyboard dialogs в theme.js.

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

После зелёных checks: review/merge PR, smoke установки по OPERATIONS.md. Live Avito/Telegram/webhook/LLM не проверены. Confidence — полнота данных, не вероятность выгодной покупки. Архивы содержат новые modules/constraints; установка требует доступности package registries.
