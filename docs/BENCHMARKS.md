# Offline baseline — 2026-10-03

Windows/Python 3.13.15, dependencies из constraints.txt. Рабочая машина с другими фоновыми задачами; значения не SLA. Synthetic input, без Avito/Telegram/LLM. Форматы запускались последовательно.

| Writer | Строк | Время | Peak process working set | Файл |
|---|---:|---:|---:|---:|
| CSV | 100 000 | 1.67 s | 40.69 MiB | 9.43 MiB |
| XLSX write-only | 100 000 | 80.76 s | 41.10 MiB | 2.78 MiB |

Вход: повторяемый batch 1000 моделей, 18 колонок. Время включает write/finish, исключает SQLite extraction, HTTP download и построение уникальных 100k моделей. Process peak учитывает интерпретатор/библиотеки. Tracemalloc выключен; `--allocations` измеряет Python allocations и замедляет XLSX.

```sh
python tools/benchmark_exports.py --rows 100000 --format csv
python tools/benchmark_exports.py --rows 100000 --format xlsx
python tools/benchmark_database.py
```

SQLite: 100k уникальных synthetic items с одним search membership, 20 последовательных замеров repository методов. SQL batch seeding без scoring/notifications исключён из времени.

| Query | p50 | p95 |
|---|---:|---:|
| Каталог count + 24 модели | 232.33 ms | 285.68 ms |
| Рыночная медиана + средняя | 661.62 ms | 775.79 ms |
| Dashboard stats | 276.23 ms | 327.22 ms |

Market snapshot считается один раз на поиск перед batch save, не для каждой карточки. До смены СУБД повторить на целевой машине с реальными фильтрами/конкуренцией. Повод профилировать: устойчивый p95 каталога >1 s, рост WAL при snapshot exports, старые pending notifications, lock timeouts. Сначала проверить индексы/query plans/batches; отдельный worker или PostgreSQL выбирать по измеренному bottleneck.
