"""Cross-process SQLite leases for a single scheduler and per-search work."""
import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from database import db


@asynccontextmanager
async def lease(name, database=db):
    owner = uuid.uuid4().hex
    now = time.time()
    async with database.connection() as connection:
        await connection.execute('BEGIN IMMEDIATE')
        row = await (await connection.execute('SELECT owner, expires_at FROM work_leases WHERE name=?', (name,))).fetchone()
        acquired = not row or row[1] < now
        if acquired:
            await connection.execute('INSERT INTO work_leases VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET owner=excluded.owner, expires_at=excluded.expires_at', (name, owner, now+60))
        await connection.commit()
    if not acquired:
        yield False
        return

    holder = asyncio.current_task()
    async def heartbeat():
        while True:
            await asyncio.sleep(15)
            try:
                async with database.connection() as connection:
                    result = await connection.execute('UPDATE work_leases SET expires_at=? WHERE name=? AND owner=?', (time.time()+60, name, owner))
                    await connection.commit()
                    if result.rowcount != 1:
                        holder.cancel()
                        return
            except Exception:
                holder.cancel()
                return

    task = asyncio.create_task(heartbeat())
    try:
        yield True
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        async with database.connection() as connection:
            await connection.execute('DELETE FROM work_leases WHERE name=? AND owner=?', (name, owner))
            await connection.commit()
