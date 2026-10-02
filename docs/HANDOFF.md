# Передача работы следующей модели

Дата: 2026-10-03. Исходный commit: 6baf52b (main).
Roadmap: https://github.com/domovoyproj/avito-parser/issues/13

## Выполнено в первом PR

- Graphify установлен/доступен; выполнены extract --code-only, query, affected, god-nodes, explain.
- graphify-out/ уже находится в .gitignore; AGENTS.md уже задаёт workflow Graphify.
- В config.py восстановлено чтение timeout/delays и send_photos после перезапуска.
- SaveSettingsRequest проверяет положительный timeout, конечные неотрицательные задержки и min <= max.
- Сохранение .env сохраняет неизвестные переменные и комментарии, экранирует значения и использует atomic replace; очистка proxy удаляет старое значение.
- Добавлены 8 изолированных unittest и шаг CI. Локально прошли config tests и test_panel_regressions.py на Windows / Python 3.13.

## Следующий шаг

Issue #1 ещё не закрывать: проверить POST /api/settings на временной .env, согласовать runtime HTTP timeout (сейчас hardcoded 20 s), проверить refresh уже созданных proxy/browser instances и не менять live config при ошибке записи. Новый PR должен отдельно тестировать эти сценарии. Затем #2 (безопасность) и #3 (CI).

## Команды

```sh
python -m venv .venv
# Windows: .venv/Scripts/python.exe; Linux: .venv/bin/python
python -m pip install -r requirements.txt
python -m pip install graphifyy
graphify extract . --code-only
graphify god-nodes
graphify query "AppConfig"
graphify affected config.py
graphify explain AppConfig
python -m unittest test_config -v
python test_panel_regressions.py
```

Использовать Python активированного venv. Graphify может быть установлен отдельно: проверять `graphify --help`, поскольку PATH и `python -m pip` могут указывать на разные окружения. После изменения структуры обновить граф. AST не доказывает runtime wiring и не индексирует HTML/templates/config в code-only режиме.

## Ограничения проверки

Сетевой сбор с Avito, Chromium, отправка Telegram/LLM и Docker не запускались. Не считать их проверенными. test_ai_scoring_engine.py пока пишет в общую БД; не запускать его без изоляции. CI на GitHub должен отдельно подтвердить Linux/Python 3.11. Невалидные числовые env значения используют defaults, обратный диапазон сбрасывает обе задержки. Повторный AppConfig.load() сохраняет приоритет уже заданного process environment; применение после перезапуска проверено через чистое окружение.
