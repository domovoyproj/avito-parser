Актуальный статус и остаток всех задач: [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md). Пользователь поручил выполнять весь roadmap, включая UI.

# Передача работы следующей модели

Дата: 2026-10-03. Исходный commit: 6baf52b (main).
Roadmap: https://github.com/domovoyproj/avito-parser/issues/13
Первый PR: https://github.com/domovoyproj/avito-parser/pull/14

## UI: дополнительное требование пользователя

Полностью обновить визуал, а не только функциональные состояния. Актуальная спецификация — Issue #10: CSS tokens и темы, общий app shell, dashboard, карточки/таблицы/фильтры, детали лота, формы настроек и мониторинг, mobile 360/768/1440 px. Прототипы и foundations можно начинать до архитектурного рефакторинга. Приёмка по screenshots до/после, единому стилю, длинным русским текстам и browser smoke на fixtures.

## Выполнено в первом PR

- Graphify установлен/доступен; выполнены extract --code-only, query, affected, god-nodes, explain.
- graphify-out/ уже находится в .gitignore; AGENTS.md уже задаёт workflow Graphify.
- В config.py восстановлено чтение timeout/delays и send_photos после перезапуска.
- SaveSettingsRequest проверяет положительный timeout, конечные неотрицательные задержки и min <= max.
- Сохранение .env сохраняет неизвестные переменные и комментарии, экранирует значения и использует atomic replace; очистка proxy удаляет старое значение.
- Добавлены 12 изолированных unittest и шаг CI. Локально прошли config tests и test_panel_regressions.py на Windows / Python 3.13.
- API сначала сохраняет staged settings, затем применяет их в памяти; при OSError возвращает 500 без раскрытия путей и без изменения live settings.
- Проверен POST/GET settings, перезапуск с чистым окружением и отказ записи через настоящий ASGI API на временной БД/.env.
- HTTP timeout и межстраничные задержки используют config. Смена/очистка proxy обновляет manager и singleton движки HTTP/Playwright; headless обновляется после успешной записи.

## Следующий шаг

Реализация и offline критерии Issue #1 выполнены в PR #14; закрывать после слияния. Следующие задачи: #2 (безопасность), #3 (CI), либо визуальные foundations #10. Настройки применяются к новым запросам/контекстам; уже открытый browser context или HTTP session не пересоздаются посреди сбора. Приложение пока рассчитано на один процесс: другие процессы не получают изменение настроек автоматически — это область #6.

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

