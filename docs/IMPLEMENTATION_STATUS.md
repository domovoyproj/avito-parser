# Статус апгрейда — 2026-10-03

Roadmap #13, реализация PR #14. Все 12 направлений получили реализацию и проверки. Issues открыты до зелёного финального CI и слияния. Production-развёртывание не выполнено.

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

## Проверки

Windows/Python 3.13: полный runner прошёл 52 unit-теста и три regression-скрипта. Затем добавлены два теста уведомлений; targeted regression повторяет monitoring/storage/security. Финальный CI запускает 54 unit-теста и три скрипта из чистых установок. Ruff/audit прошли, известных уязвимостей в locked runtime dependencies не обнаружено.

Browser: 11 страниц × 3 ширины, themes, search creation, favorite, background CSV download, filter/empty/error/retry, long XSS text, viewer/operator denial, keyboard dialogs; нет JS exceptions/horizontal overflow. Before/after screenshots вне репозитория: `work/ui-before`, `work/ui-final`. CI публикует after screenshots artifact `ui-screenshots`. OpenAPI equality относится к refactoring; новые jobs/metrics/score fields добавлены намеренно.

Измерения: [BENCHMARKS.md](BENCHMARKS.md). Docker локально отсутствует, результат определяется GitHub CI. Live Avito/Telegram/webhook/LLM не запускались. Synthetic scoring fixtures не подтверждают качество реального рынка; confidence означает полноту данных. Enqueue time старого outbox оценивается временем миграции, поскольку исходных timestamps не было. Process env имеет приоритет над .env; после изменения настроек в одном процессе остальные перезапустить.

Далее: финальный зелёный CI → review/merge PR #14 → smoke установки по OPERATIONS.md. Не закрывать эпики по одному промежуточному commit.
