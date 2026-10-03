# Статус апгрейда — 2026-10-03

Работа продолжается по Issues #1–12, общий roadmap #13. Интеграционный PR #14. Частично выполненные эпики не закрываются.

| Issue | Уже реализовано | Оставшиеся критерии |
|---|---|---|
| #1 | Валидация, atomic .env, runtime engines, rollback, 12 тестов | Merge и проверка реальной установки |
| #2 | Bootstrap без известного пароля, rate limit, CSRF/origin, роли, cookie flags, отзыв сессий, проверка WS | Тест отзыва открытого WS, аудит bot commands и полного UI XSS |
| #3 | Constraints, общий offline runner, критический lint, audit, Windows/Linux matrix | Дождаться зелёных CI, проверить поддержку всех версий |
| #4 | WAL, FK, busy timeout, атомарное сохранение/history, memberships, версии схемы, backup | Полный аудит агрегатов memberships, fixture старой схемы, проверка будущей версии до DDL |
| #5 | JSON/URI/DOM fixtures, malformed cards, нулевая цена, typed outcomes | Дополнить fixtures деталей и callback contracts |
| #6 | Общий сервис web/bot, local locks и SQLite leases, concurrency 3, timeout, blocked/partial без stale cleanup | Перевести CLI, per-search due scheduling, lost lease cancellation, лимиты parser jobs |
| #7 | Transactional outbox по получателю, retries, lease recovery, Retry-After, quiet hours, formatter | Crash recovery fixtures, JSON Retry-After Telegram, метрики очереди, complete formatter limits |
| #8 | Auth/settings routers, auth dependencies, monitoring/notification services; OpenAPI неизменён | Остальные routers, repositories и полноценная DI |
| #9 | Исправленная медиана, закрытые исключены, None price LLM, ограниченный cache/budget и безопасные LLM logs | Постоянный общий cache/budget, версия/confidence score, маскирование secrets и тесты провайдера |
| #10 | Локальные Tailwind/Lucide/Chart, tokens, две темы, app shell, общий стиль, безопасный вывод основных полей; browser smoke 360/768/1440 | Focus trap/keyboard, все страницы и роли, визуальный QA деталей/форм/ошибок |
| #11 | Excel колонки, CSV URL/zero, formula injection, HTML escape/safe URL, filename uniqueness, thread export, явный limit 10k, online backup | Background exports, streaming XLSX, backup CLI, 100k benchmark и restore drill |
| #12 | Health/ready, non-root image, .dockerignore, Docker CI smoke, operations guide | Метрики, полная редактировка логов, release smoke и Docker CI результаты |

Локально: offline suite (28 unit tests до последних дополнительных изменений плюс 3 scripts) прошла; отдельно 6 storage/export tests прошли. Ruff прошёл. pip-audit: No known vulnerabilities found. Browser smoke прошёл для 6 страниц, трёх ширин, filtering и сохранения light theme, с проверкой настоящего fixture count=24. OpenAPI before/after выделения routers полностью совпал. Локально Docker отсутствует: сборка проверяется CI, результат нельзя считать подтверждённым заранее.

Следующей модели: сначала прочитать AGENTS.md и этот файл, затем выбранный Issue. Запустить Graphify query/affected/explain до анализа. Не объявлять roadmap завершённым по наличию базовой реализации. Приоритет продолжения: CI → #6 CLI/due/jobs → #4 memberships/migrations → #7 crash fixtures → #9 persistent budget/secrets → #10 keyboard/visual → #11 large exports → #12 metrics/release.
