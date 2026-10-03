"""SQLite-backed at-least-once delivery; a crash after send may duplicate it."""
import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import datetime
import httpx
import aiosqlite
from config import config
from database import db
from models import AvitoItem
from notifications import format_item_notification
from security import avito_url


def quiet_now(settings, hour):
    if not settings.quiet_hours_enabled:
        return False
    start, end = settings.quiet_hours_start, settings.quiet_hours_end
    return start == end or (start <= hour < end if start < end else hour >= start or hour < end)


class OutboxWorker:
    def __init__(self, database=db):
        self.db = database

    async def claim(self):
        now = time.time()
        async with self.db.connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            connection.row_factory = aiosqlite.Row
            row = await (await connection.execute("SELECT * FROM notification_outbox WHERE available_at<=? AND (status='pending' OR (status='sending' AND lease_until<?)) ORDER BY id LIMIT 1", (now, now))).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            await connection.execute("UPDATE notification_outbox SET status='sending', lease_token=?, lease_until=? WHERE id=?", (token, now+60, row['id']))
            await connection.commit()
            return dict(row) | {"lease_token": token}

    async def finish(self, job, status, delay=0, error="", attempted=True):
        async with self.db.connection() as connection:
            await connection.execute("UPDATE notification_outbox SET status=?, attempts=attempts+?, available_at=?, lease_until=0, last_error=? WHERE id=? AND lease_token=?", (status, int(attempted), time.time()+delay, error[:100], job['id'], job['lease_token']))
            await connection.commit()

    async def deliver(self, job):
        try:
            payload = json.loads(job['payload'])
            item = AvitoItem.model_validate(payload['item'])
        except (ValueError, KeyError, TypeError):
            await self.finish(job, 'failed', error='invalid payload')
            return
        retry = min(3600, 30 * (2 ** min(job['attempts'], 7)))
        try:
            if item.seller and item.seller.name in await self.db.get_blacklisted_sellers():
                await self.finish(job, 'skipped', attempted=False)
                return
            async with httpx.AsyncClient(timeout=15) as client:
                if job['channel'] == 'telegram':
                    if not config.telegram.bot_token:
                        await self.finish(job, 'pending', 300, 'telegram disabled', attempted=False)
                        return
                    chat = int(job['destination'])
                    settings = await self.db.get_chat_settings(chat)
                    if quiet_now(settings, datetime.now().hour):
                        await self.finish(job, 'pending', 300, 'quiet hours', attempted=False)
                        return
                    skipped = ((payload['type'] == 'new' and not settings.notify_new) or
                               (payload['type'] == 'price_drop' and not settings.notify_drops) or
                               (settings.delivery_only and not item.delivery_available) or
                               ((item.deal_score or 0) < settings.min_deal_score))
                    if skipped:
                        await self.finish(job, 'skipped')
                        return
                    if payload['type'] == 'price_drop' and settings.min_discount_pct and item.old_price and item.price is not None:
                        if 100 * (item.old_price-item.price) / item.old_price < settings.min_discount_pct:
                            await self.finish(job, 'skipped')
                            return
                    if settings.only_below_market and item.price is not None:
                        market = await self.db.get_search_market_price(item.search_query_id)
                        if not market or item.price > market * (1-settings.below_market_pct/100):
                            await self.finish(job, 'skipped')
                            return
                    text = await format_item_notification(item, payload['search_name'], payload['type'])
                    message = {'chat_id': chat, 'text': text, 'parse_mode': 'HTML'}
                    if avito_url(item.url):
                        message['reply_markup'] = {'inline_keyboard': [[{'text': 'Открыть объявление', 'url': item.url}]]}
                    endpoint = 'sendMessage'
                    if config.telegram.send_photos and settings.send_photos and item.main_image and len(text) < 1000:
                        endpoint = 'sendPhoto'
                        message['photo'] = item.main_image
                        message['caption'] = message.pop('text')
                    response = await client.post(f'https://api.telegram.org/bot{config.telegram.bot_token}/{endpoint}', json=message)
                    if endpoint == 'sendPhoto' and response.status_code == 400:
                        message['text'] = message.pop('caption')
                        message.pop('photo')
                        response = await client.post(f'https://api.telegram.org/bot{config.telegram.bot_token}/sendMessage', json=message)
                    delivered = response.status_code == 200 and response.json().get('ok')
                else:
                    settings = await self.db.get_webhook_settings()
                    if not settings.enabled or settings.url != job['destination']:
                        await self.finish(job, 'skipped')
                        return
                    body = json.dumps(payload, ensure_ascii=False).encode()
                    headers = {'Content-Type': 'application/json'}
                    if settings.secret:
                        headers['X-Webhook-Signature'] = hmac.new(settings.secret.encode(), body, hashlib.sha256).hexdigest()
                    response = await client.post(job['destination'], content=body, headers=headers)
                    delivered = 200 <= response.status_code < 300
                if delivered:
                    await self.finish(job, 'sent')
                else:
                    if response.status_code == 429:
                        try:
                            server_retry = response.headers.get('Retry-After')
                            if server_retry is None:
                                server_retry = response.json().get('parameters', {}).get('retry_after', retry)
                            retry = max(1, min(86400, float(server_retry)))
                        except (ValueError, TypeError, AttributeError):
                            pass
                    permanent = (400 <= response.status_code < 500 and response.status_code != 429) or job['attempts'] >= 7
                    await self.finish(job, 'failed' if permanent else 'pending', retry, f'HTTP {response.status_code}')
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            await self.finish(job, 'failed' if job['attempts'] >= 7 else 'pending', retry, 'delivery error')

    async def drain(self, limit=20):
        for _ in range(limit):
            job = await self.claim()
            if not job:
                break
            await self.deliver(job)

    async def run(self):
        while True:
            try:
                await self.drain()
            except Exception as exc:
                logging.getLogger('AvitoOutbox').error('Outbox cycle failed: %s', type(exc).__name__)
            await asyncio.sleep(5)


outbox_worker = OutboxWorker()
