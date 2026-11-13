import json
import unittest
from unittest.mock import AsyncMock, patch
from config import config
from database import db
from models import AvitoItem, WebhookSettings
from outbox import OutboxWorker
import test_storage
import test_config


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_storage.StorageTests.asyncSetUp

    async def enqueue(self):
        telegram = config.telegram.model_copy(deep=True)
        telegram.bot_token, telegram.admin_chat_ids = 'fixture-only', [1]
        with patch.object(config, 'telegram', telegram):
            await self.db.save_item(AvitoItem(id='deal', title='Fixture', price=100, url='https://www.avito.ru/fixture'))
        return OutboxWorker(self.db)

    async def test_stale_price_or_closed_item_never_reaches_network(self):
        worker = await self.enqueue()
        async with self.db.connection() as connection:
            await connection.execute("UPDATE items SET price=90 WHERE id='deal'")
            await connection.commit()
        with patch('outbox.httpx.AsyncClient') as client:
            await worker.deliver(await worker.claim())
            client.assert_not_called()
        data = await self.db.get_notification_events()
        self.assertEqual(data['counts'], {'skipped':1})
        self.assertEqual(data['events'][0]['last_error'], 'stale or unavailable')

    async def test_webhook_honors_minimum_score(self):
        worker = await self.enqueue()
        async with self.db.connection() as connection:
            await connection.execute("UPDATE notification_outbox SET channel='webhook',destination='https://example.com/hook'")
            await connection.commit()
        settings=WebhookSettings(enabled=True,url='https://example.com/hook',min_deal_score=100)
        with patch.object(self.db,'get_webhook_settings',AsyncMock(return_value=settings)), patch('outbox.httpx.AsyncClient') as client:
            client.return_value.__aenter__.return_value.post=AsyncMock()
            await worker.deliver(await worker.claim())
            client.return_value.__aenter__.return_value.post.assert_not_awaited()
        self.assertEqual((await self.db.get_notification_events())['counts'], {'skipped':1})

    async def test_retry_is_atomic_failed_only_and_hides_destinations(self):
        worker = await self.enqueue()
        job = await worker.claim()
        await worker.finish(job,'failed',error='HTTP 403')
        self.assertTrue(await self.db.retry_notification(job['id']))
        self.assertFalse(await self.db.retry_notification(job['id']))
        event = (await self.db.get_notification_events())['events'][0]
        self.assertEqual((event['status'],event['attempts']),('pending',0))
        self.assertNotIn('payload', event)
        self.assertNotIn('destination', event)

    async def test_malformed_event_is_still_visible_in_history(self):
        async with self.db.connection() as connection:
            await connection.execute("INSERT INTO notification_outbox(event_key,channel,destination,payload) VALUES ('bad','telegram','1','[]')")
            await connection.commit()
        self.assertEqual(len((await self.db.get_notification_events())['events']),1)

    async def test_quiet_hours_are_persisted_and_preserved_by_bot_updates(self):
        await self.db.update_chat_settings(1,min_deal_score=70,quiet_hours_enabled=True,quiet_hours_start=22,quiet_hours_end=8)
        await self.db.update_chat_settings(1,send_photos=False)
        from database import Database
        settings=await Database(self.db.db_path).get_chat_settings(1)
        self.assertEqual((settings.quiet_hours_enabled,settings.quiet_hours_start,settings.quiet_hours_end),(True,22,8))
        self.assertEqual(settings.min_deal_score,70)
        self.assertFalse(settings.send_photos)


class NotificationAPITests(unittest.IsolatedAsyncioTestCase):
    setUp = test_config.SettingsAPITests.setUp
    asyncSetUp = test_config.SettingsAPITests.asyncSetUp

    async def test_preferences_allow_configured_recipients_only_and_validate_thresholds(self):
        with patch.object(config.telegram,'admin_chat_ids',[777]):
            response=await self.client.get('/api/notifications/preferences')
            self.assertEqual(response.json()['chats'][0]['chat_id'],777)
            response=await self.client.post('/api/notifications/preferences/777',json={'min_deal_score':70,'quiet_hours_enabled':True})
            self.assertEqual(response.status_code,200)
            self.assertTrue((await db.get_chat_settings(777)).quiet_hours_enabled)
            self.assertEqual((await self.client.post('/api/notifications/preferences/778',json={})).status_code,404)
            self.assertEqual((await self.client.post('/api/notifications/preferences/777',json={'min_deal_score':101})).status_code,422)

    async def test_api_validates_filters_and_requires_admin(self):
        self.assertEqual((await self.client.get('/api/notifications')).status_code,200)
        self.assertEqual((await self.client.get('/api/notifications?status=unknown')).status_code,422)
        self.assertEqual((await self.client.post('/api/notifications/1/retry')).status_code,409)
        from models import UserRole
        await db.create_user('viewer', 'fixture-viewer-password', UserRole.VIEWER)
        await self.client.post('/api/auth/logout')
        await self.client.post('/api/auth/login',json={'username':'viewer','password':'fixture-viewer-password'})
        self.client.headers['X-CSRF-Token']=self.client.cookies['avito_csrf']
        self.assertEqual((await self.client.get('/api/notifications')).status_code,403)
        self.assertEqual((await self.client.get('/notifications')).status_code,303)


if __name__ == '__main__':
    unittest.main()
