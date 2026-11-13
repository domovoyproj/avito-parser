"""Operational notification data, without tokens, recipients or raw payloads."""
import json
import time
import aiosqlite


class NotificationEventsRepository:
    async def get_notification_events(self, status=None, limit=50):
        async with self.connection() as connection:
            connection.row_factory = aiosqlite.Row
            counts = {row['status']: row['count'] for row in await (await connection.execute(
                'SELECT status,COUNT(*) AS count FROM notification_outbox GROUP BY status')).fetchall()}
            where, args = ('WHERE status=?', [status]) if status else ('', [])
            rows = await (await connection.execute(
                f'SELECT id,channel,payload,status,attempts,created_at,available_at,last_error '
                f'FROM notification_outbox {where} ORDER BY id DESC LIMIT ?', [*args, limit])).fetchall()
        events = []
        for row in rows:
            try:
                payload = json.loads(row['payload'])
                item = payload.get('item', {})
                if not isinstance(item, dict) or not isinstance(payload, dict):
                    raise ValueError('invalid event')
            except (ValueError, TypeError, AttributeError):
                payload, item = {}, {}
            events.append({key: row[key] for key in ('id', 'channel', 'status', 'attempts', 'created_at', 'available_at', 'last_error')} | {
                'title': str(item.get('title') or 'Объявление недоступно')[:200],
                'price': item.get('price'), 'score': item.get('deal_score'),
                'url': item.get('url'), 'type': payload.get('type'),
                'search_name': str(payload.get('search_name') or 'Поиск')[:100]})
        return {'counts': counts, 'events': events}

    async def retry_notification(self, event_id):
        async with self.connection() as connection:
            result = await connection.execute(
                "UPDATE notification_outbox SET status='pending', attempts=0, available_at=?, "
                "lease_until=0, lease_token=NULL, last_error=NULL WHERE id=? AND status='failed'",
                (time.time(), event_id))
            await connection.commit()
            return result.rowcount == 1
