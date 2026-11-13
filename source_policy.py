"""Shared, durable request pacing. A blocked source is paused, never bypassed."""
import asyncio
import math
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from config import config
from database import db


def retry_seconds(value, default=300):
    """Accept both Retry-After formats; malformed values cannot disable backoff."""
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            delay = date.timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            delay = default
    if not math.isfinite(delay):
        delay = default
    return max(1, min(86400, math.ceil(delay)))


def page_url(url, page):
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != 'p']
    query.append(('p', str(page)))
    return urlunsplit(parts._replace(query=urlencode(query)))


class SourceCooldown(Exception):
    def __init__(self, retry_after):
        self.retry_after = retry_after
        super().__init__(f'Источник на паузе ещё {retry_after} сек. после ограничения доступа')


class SourcePolicy:
    def __init__(self, database=db):
        self.db = database

    async def status(self):
        async with self.db.connection() as connection:
            row = await (await connection.execute(
                "SELECT blocked_until, failures FROM source_access_state WHERE source='avito'"
            )).fetchone()
        until, failures = row if row else (0, 0)
        return {'paused': until > time.time(), 'retry_at': until,
                'retry_after_seconds': max(0, math.ceil(until - time.time())), 'failures': failures}

    async def wait(self, manual=False):
        # Reserve only the current slot. Waiting callers recheck the breaker so
        # another search's 403 stops the whole queue, including other processes.
        while True:
            async with self.db.connection() as connection:
                await connection.execute('BEGIN IMMEDIATE')
                now = time.time()
                row = await (await connection.execute(
                    "SELECT next_request_at, blocked_until FROM source_access_state WHERE source='avito'"
                )).fetchone()
                next_at, blocked_until = row if row else (0, 0)
                if blocked_until > now and not manual:
                    raise SourceCooldown(math.ceil(blocked_until - now))
                delay = next_at - now
                if delay <= 0:
                    interval = max(1, config.scraper.page_delay_min)
                    await connection.execute(
                        "INSERT INTO source_access_state(source,next_request_at) VALUES ('avito',?) "
                        "ON CONFLICT(source) DO UPDATE SET next_request_at=excluded.next_request_at",
                        (now + interval,))
                    await connection.commit()
                    return
            await asyncio.sleep(min(delay, 1))

    async def block(self, retry_after=None):
        async with self.db.connection() as connection:
            await connection.execute('BEGIN IMMEDIATE')
            row = await (await connection.execute(
                "SELECT failures, blocked_until FROM source_access_state WHERE source='avito'"
            )).fetchone()
            now = time.time()
            failures, previous_until = row if row else (0, 0)
            # Concurrent requests observing the same block do not compound it.
            failures += int(previous_until <= now)
            delay = max((300, 900, 3600)[min(max(failures - 1, 0), 2)], retry_seconds(retry_after))
            until = max(previous_until, now + delay)
            await connection.execute(
                "INSERT INTO source_access_state(source,blocked_until,failures) VALUES ('avito',?,?) "
                "ON CONFLICT(source) DO UPDATE SET blocked_until=excluded.blocked_until, failures=excluded.failures",
                (until, failures))
            await connection.commit()
        return math.ceil(until - now)

    async def success(self, manual=False):
        async with self.db.connection() as connection:
            await connection.execute(
                "UPDATE source_access_state SET blocked_until=0, failures=0 "
                "WHERE source='avito' AND (blocked_until<=? OR ?)", (time.time(), int(manual)))
            await connection.commit()


source_policy = SourcePolicy()
