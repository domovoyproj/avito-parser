"""Persistent background exports, serialized by a renewable SQLite lease."""

import asyncio
import json
import secrets
import time
from config import config
from coordination import lease
from database import db
from streaming_export import StreamingExport
from catalog_filters import catalog_where

MAX_ROWS = 1000000


async def complete_thread(function, *args):
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class ExportJobs:
    def __init__(self, database=db, directory=None):
        self.db = database
        self.directory = directory or config.export_dir

    async def enqueue(self, fmt, filters):
        async with self.db.connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            count = await (
                await connection.execute(
                    "SELECT COUNT(*) FROM export_jobs WHERE status IN ('pending','running')"
                )
            ).fetchone()
            if count[0] >= 20:
                raise ValueError("Очередь экспорта заполнена")
            job_id = secrets.token_hex(16)
            await connection.execute(
                "INSERT INTO export_jobs(id,format,filters,status,created_at) VALUES (?,?,?,'pending',?)",
                (job_id, fmt, json.dumps(filters), time.time()),
            )
            await connection.commit()
            return job_id

    async def get(self, job_id):
        async with self.db.connection() as connection:
            connection.row_factory = __import__("aiosqlite").Row
            row = await (
                await connection.execute(
                    "SELECT * FROM export_jobs WHERE id=?", (job_id,)
                )
            ).fetchone()
            return dict(row) if row else None

    async def process_one(self):
        async with lease("export-worker", database=self.db) as acquired:
            if not acquired:
                return False
            async with self.db.connection() as connection:
                connection.row_factory = __import__("aiosqlite").Row
                row = await (
                    await connection.execute(
                        "SELECT * FROM export_jobs WHERE status IN ('pending','running') ORDER BY created_at LIMIT 1"
                    )
                ).fetchone()
                if not row:
                    return False
                job = dict(row)
                await connection.execute(
                    "UPDATE export_jobs SET status='running', rows_written=0 WHERE id=?",
                    (job["id"],),
                )
                await connection.commit()
            path = self.directory / f"export_{job['id']}.{job['format']}"
            temporary = path.with_suffix(path.suffix + ".part")
            self.directory.mkdir(parents=True, exist_ok=True)
            writer = None
            try:
                filters = json.loads(job["filters"])
                where, params, _, _ = catalog_where(**filters)
                async with self.db.connection() as connection:
                    connection.row_factory = __import__("aiosqlite").Row
                    await connection.execute("BEGIN")
                    count = await (
                        await connection.execute(
                            f"SELECT COUNT(*) FROM items WHERE {where}", params
                        )
                    ).fetchone()
                    if count[0] > MAX_ROWS:
                        raise ValueError("Выборка превышает миллион строк")
                    writer = StreamingExport(temporary, job["format"])
                    cursor = await connection.execute(
                        f"SELECT * FROM items WHERE {where} ORDER BY id", params
                    )
                    written = 0
                    while batch := await cursor.fetchmany(1000):
                        await complete_thread(
                            writer.write, [self.db._row_to_item(row) for row in batch]
                        )
                        written += len(batch)
                        async with self.db.connection() as progress:
                            await progress.execute(
                                "UPDATE export_jobs SET rows_written=? WHERE id=?",
                                (written, job["id"]),
                            )
                            await progress.commit()
                await complete_thread(writer.finish)
                temporary.replace(path)
                async with self.db.connection() as connection:
                    await connection.execute(
                        "UPDATE export_jobs SET status='completed', finished_at=? WHERE id=?",
                        (time.time(), job["id"]),
                    )
                    await connection.commit()
            except asyncio.CancelledError:
                if writer:
                    await complete_thread(writer.abort)
                async with self.db.connection() as connection:
                    await connection.execute(
                        "UPDATE export_jobs SET status='pending' WHERE id=?",
                        (job["id"],),
                    )
                    await connection.commit()
                raise
            except Exception as exc:
                if writer:
                    await complete_thread(writer.abort)
                async with self.db.connection() as connection:
                    await connection.execute(
                        "UPDATE export_jobs SET status='failed',error=?,finished_at=? WHERE id=?",
                        (type(exc).__name__, time.time(), job["id"]),
                    )
                    await connection.commit()
            return True

    async def run(self):
        while True:
            try:
                await self.process_one()
            except Exception:
                pass
            await asyncio.sleep(2)


export_jobs = ExportJobs()
