"""Reproducible 100k-row LIKE/FTS5 benchmark: python tools/benchmark_search.py"""

import json
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path


ROWS = 100_000
QUERIES = ("велосипед", "iphone", "диван", "самокат", "камера", "шкаф")


def percentile(values, fraction):
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))], 3)


def benchmark():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "search.db"
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE items(title TEXT,description TEXT,address TEXT,params_json TEXT)")
        titles = ("Горный велосипед", "iPhone 15 Pro", "Диван раскладной", "Электросамокат", "Камера Sony", "Шкаф купе")
        start = time.perf_counter()
        db.executemany("INSERT INTO items VALUES (?,?,?,?)", ((titles[i % 6] + f" {i}", "Хорошее состояние, доставка по городу", "Москва", '{}') for i in range(ROWS)))
        db.commit()
        insert_seconds = round(time.perf_counter() - start, 3)
        base_size = path.stat().st_size
        results = {"rows": ROWS, "sqlite": sqlite3.sqlite_version, "base_bytes": base_size, "insert_seconds": insert_seconds}
        for mode in ("like", "trigram", "unicode61"):
            if mode != "like":
                used_before = (db.execute("PRAGMA page_count").fetchone()[0] - db.execute("PRAGMA freelist_count").fetchone()[0]) * db.execute("PRAGMA page_size").fetchone()[0]
                db.execute(f"CREATE VIRTUAL TABLE fts USING fts5(title,description,address,params_json, content='items',content_rowid='rowid', tokenize='{mode}')")
                start = time.perf_counter()
                db.execute("INSERT INTO fts(fts) VALUES('rebuild')")
                db.commit()
                build_seconds = round(time.perf_counter() - start, 3)
                used_after = (db.execute("PRAGMA page_count").fetchone()[0] - db.execute("PRAGMA freelist_count").fetchone()[0]) * db.execute("PRAGMA page_size").fetchone()[0]
                size = used_after - used_before
            else:
                build_seconds, size = 0, 0
            times = []
            for _ in range(10):
                for query in QUERIES:
                    start = time.perf_counter()
                    if mode == "like":
                        db.execute("SELECT COUNT(*) FROM items WHERE title LIKE ? OR description LIKE ? OR address LIKE ? OR params_json LIKE ?", (f'%{query}%',) * 4).fetchone()
                    elif mode == "trigram":
                        db.execute("SELECT COUNT(*) FROM fts WHERE title LIKE ? OR description LIKE ? OR address LIKE ? OR params_json LIKE ?", (f'%{query}%',) * 4).fetchone()
                    else:
                        db.execute("SELECT COUNT(*) FROM fts WHERE fts MATCH ?", (query,)).fetchone()
                    times.append((time.perf_counter() - start) * 1000)
            results[mode] = {"p50_ms": percentile(times, .5), "p95_ms": percentile(times, .95), "index_bytes": size, "build_seconds": build_seconds}
            if mode != "like":
                start = time.perf_counter()
                for index in range(1000):
                    title = f"Бенчмарк объявление {index}"
                    cursor = db.execute("INSERT INTO items VALUES (?,?,?,?)", (title, "Описание", "Москва", '{}'))
                    db.execute("INSERT INTO fts(rowid,title,description,address,params_json) VALUES (?,?,?,?,?)",
                               (cursor.lastrowid, title, "Описание", "Москва", '{}'))
                db.commit()
                results[mode]["insert_1000_seconds"] = round(time.perf_counter() - start, 3)
                start = time.perf_counter()
                db.execute("INSERT INTO fts(fts) VALUES('rebuild')")
                db.commit()
                results[mode]["rebuild_seconds"] = round(time.perf_counter() - start, 3)
                db.execute("DROP TABLE fts")
                db.commit()
        db.close()
        return results


if __name__ == "__main__":
    print(json.dumps(benchmark(), ensure_ascii=False, indent=2))
