"""Persistent cache and atomic daily request budget shared across processes."""

import time
from database import db


async def cached_verdict(key):
    async with db.connection() as connection:
        row = await (
            await connection.execute(
                "SELECT verdict FROM llm_cache WHERE cache_key=? AND expires_at>?",
                (key, time.time()),
            )
        ).fetchone()
        return row[0] if row else None


async def reserve_budget(day, limit=100):
    async with db.connection() as connection:
        await connection.execute("BEGIN IMMEDIATE")
        await connection.execute(
            "INSERT OR IGNORE INTO llm_budget VALUES (?,0)", (day,)
        )
        result = await connection.execute(
            "UPDATE llm_budget SET requests=requests+1 WHERE day=? AND requests<?",
            (day, limit),
        )
        await connection.commit()
        return result.rowcount == 1


async def save_verdict(key, verdict):
    async with db.connection() as connection:
        await connection.execute(
            "INSERT OR REPLACE INTO llm_cache VALUES (?,?,?)",
            (key, verdict, time.time() + 86400),
        )
        await connection.execute(
            "DELETE FROM llm_cache WHERE expires_at<=?", (time.time(),)
        )
        await connection.execute(
            "DELETE FROM llm_cache WHERE cache_key NOT IN (SELECT cache_key FROM llm_cache ORDER BY expires_at DESC LIMIT 1024)"
        )
        await connection.commit()
