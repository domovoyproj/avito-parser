# Статус апгрейда — 2026-10-04

Roadmap #13, базовая реализация [PR #14](https://github.com/domovoyproj/avito-parser/pull/14) влита. Дополнительные направления: история мониторинга [PR #19](https://github.com/domovoyproj/avito-parser/pull/19), личные списки [PR #20](https://github.com/domovoyproj/avito-parser/pull/20), обратная связь о скоринге [PR #21](https://github.com/domovoyproj/avito-parser/pull/21) и поиск [PR #22](https://github.com/domovoyproj/avito-parser/pull/22) влиты и прошли CI. Production-развёртывание и live-интеграции не выполнены; Issues остаются открыты до проверки установки.

| Issue | Реализовано |
|---|---|
| #1 | .env/API/restart round-trip; finite/range validation; atomic replace/comments; rollback live settings; proxy/headless/timeout/delays; 12 tests |
| #2 | Одноразовый bootstrap без стандартного пароля; отключение legacy admin/admin123 и local recovery; rate limit, CSRF/origin/cookie/roles; session expiry/revoke и открытый WS; bot identity guards; XSS fixtures |
| #3 | Constraints с Python/platform markers; isolated runner; Windows/Linux 3.10/3.11/3.13; lint/audit; local UI deps и browser CI |
| #4 | WAL/FK/busy timeout; atomic item/history/membership/outbox; actual legacy fixture/idempotent migration/future guard; concurrent writes; membership aggregates; backup при записи/restore drill |
| #5 | Raw/URI JSON, DOM/details, malformed cards/priceDetailed, free/zero price; success/empty/blocked/partial/error; unknown layout error; blocked без browser fallback |
| #6 | Shared web/bot/CLI scheduler; locks/renewable leases/owner checks; concurrency 3; due/active hours; cancel/timeout/shutdown; parser job limits; lost lease fixtures; no false stale cleanup |
| #7 | Outbox per recipient; atomic enqueue, recovery/CAS, 429 Retry-After, retry max 8, quiet hours; invalid payload failure; seller blacklist; bounded formatter/zero price; at-least-once documented |
| #8 | 8 routers, repositories, monitoring/parser/export services; FastAPI DI; OpenAPI equality для переносов; dependency override test |
| #9 | Even median, excludes hidden/closed/zero; score version/confidence/factors; one market snapshot/batch; SQLite cache TTL/cap и atomic budget; masked key; provider 429/timeout/cache fixtures |
| #10 | App shell/dashboard/catalog/forms, dark/light tokens, local assets; 360/768/1440; keyboard/focus/Escape; real API/error/retry/empty/loading; race-safe filtering; roles; 11-page browser QA |
| #11 | Safe exports/formula injection/URL/zero; legacy limit; durable streaming CSV/XLSX batches 1000/recovery; unassigned filter/range validation; 100k benchmark; backup/restore CLI |
| #12 | Health/ready; admin queue age/errors/duration/jobs/DB metrics; redacted JSON run/search logs; non-root Docker/writable volumes; restart CI; release archive/import smoke; runbooks/baseline |

## Дополнительные задачи

| Issue | Реализовано и проверено локально |
|---|---|
| #17 | Schema 7; история каждого цикла и поиска с исходом, движком, длительностью и счётчиками; 30-дневное хранение; timeline/фильтры в UI. PR #19, CI PASS. |
| #15 | Schema 8; независимые watchlists двух пользователей, миграция глобального избранного, порог цены, заметка/пауза, одноразовая привязка Telegram и дедупликация outbox. PR #20, CI PASS. |
| #16 | Schema 9; неизменяемые снимки меток с автором/версией оценки, отчёт по категории/поиску/версии, обезличенный offline-экспорт. PR #21, CI PASS. |
| #18 | Schema 10; FTS5 `unicode61` с backfill и триггерами, общий фильтр каталога/экспорта, BM25, безопасная подсветка, персональные сохранённые фильтры/ссылки. PR #22, CI PASS. |

## Проверки

Windows/Python 3.13: на состоянии PR #22 полный runner прошёл 68 unit-тестов и три regression-скрипта. Ruff и `git diff --check` прошли. CI PR #19–#22 зелёный. Ранее audit не обнаружил известных уязвимостей в locked runtime dependencies.

Browser: 13 страниц × 3 ширины, темы, поиск и безопасная подсветка, сохранённые фильтры, feedback, watchlists, background CSV download, filter/empty/error/retry, long XSS text, viewer/operator denial, keyboard dialogs; нет JS exceptions/horizontal overflow. Локальные screenshots — вне Git в `ui-artifacts/`; CI публикует `ui-screenshots`. OpenAPI equality относится к refactoring; новые API добавлены намеренно.

Измерения: [BENCHMARKS.md](BENCHMARKS.md). Docker локально отсутствует, результат определяется GitHub CI. Live Avito/Telegram/webhook/LLM не запускались. Synthetic scoring fixtures не подтверждают качество реального рынка; confidence означает полноту данных. Enqueue time старого outbox оценивается временем миграции, поскольку исходных timestamps не было. Process env имеет приоритет над .env; после изменения настроек в одном процессе остальные перезапустить.

На синтетических 100 000 строках SQLite 3.50.4: `unicode61` p50/p95 0,842/1,383 мс и индекс 5,94 МБ; `%LIKE%` 65,382/79,771 мс; `trigram` 48,934/76,154 мс и 25,7 МБ. Повторить на реальном каталоге. Далее: backup/проверка миграций на копии реальной БД → smoke установки и live-интеграций по OPERATIONS.md. Не закрывать эпики без внешней проверки.
