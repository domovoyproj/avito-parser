import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from database import db
from models import AvitoItem, ParseResult, SearchQuery, TelegramChatSettings
from monitoring import MonitoringService
from coordination import lease
from outbox import OutboxWorker, quiet_now
import test_storage
import config
import monitoring


class MonitoringTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await test_storage.StorageTests.asyncSetUp(self)
        context = patch.object(db, 'db_path', self.db.db_path)
        context.start()
        self.addCleanup(context.stop)

    async def test_lease_excludes_other_instance_and_releases_on_cancel(self):
        async with lease('fixture') as acquired:
            self.assertTrue(acquired)
            async with lease('fixture') as other:
                self.assertFalse(other)
        async with lease('fixture') as acquired:
            self.assertTrue(acquired)

    async def test_concurrent_check_blocked_and_no_false_cleanup(self):
        search_id = await db.add_search(SearchQuery(name='fixture', url='https://www.avito.ru/fixture'))
        search = await db.get_search_by_id(search_id)
        service = MonitoringService()
        started, release = asyncio.Event(), asyncio.Event()

        async def parse(*args, **kwargs):
            started.set()
            await release.wait()
            return ParseResult(outcome='blocked', errors=['fixture'])

        with patch.object(monitoring.http_engine, 'parse_search', side_effect=parse), \
             patch.object(monitoring.browser_engine, 'parse_search', new_callable=AsyncMock) as browser, \
             patch.object(db, 'cleanup_stale_items', new_callable=AsyncMock) as cleanup:
            first = asyncio.create_task(service.check_search(search))
            await started.wait()
            self.assertEqual((await service.check_search(search))['skipped'], 1)
            release.set()
            self.assertEqual((await first)['errors'], 1)
            browser.assert_not_awaited()
            cleanup.assert_not_awaited()

    async def test_outbox_commit_per_recipient_retry_and_delivery(self):
        settings = config.config.telegram.model_copy(deep=True)
        settings.bot_token = 'fixture-only'
        settings.admin_chat_ids = [1, 2]
        with patch.object(config.config, 'telegram', settings):
            await self.db.save_item(AvitoItem(id='notify', title='fixture', price=100, url='https://www.avito.ru/fixture'))
        worker = OutboxWorker(self.db)
        one, two = await asyncio.gather(worker.claim(), worker.claim())
        self.assertNotEqual(one['id'], two['id'])
        self.assertIsNone(await worker.claim())
        response = httpx.Response(429, headers={'Retry-After': '45'})
        client = MagicMock()
        client.post = AsyncMock(return_value=response)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch.object(config.config, 'telegram', settings), patch('outbox.httpx.AsyncClient', return_value=client):
            await worker.deliver(one)
            client.post.return_value = httpx.Response(200, json={'ok': True})
            await worker.deliver(two)
        async with self.db.connection() as connection:
            rows = await (await connection.execute('SELECT status, attempts FROM notification_outbox ORDER BY id')).fetchall()
        self.assertEqual(rows, [('pending', 1), ('sent', 1)])

    def test_quiet_hours_day_and_night(self):
        settings = TelegramChatSettings(chat_id=1, quiet_hours_enabled=True, quiet_hours_start=9, quiet_hours_end=17)
        self.assertTrue(quiet_now(settings, 12))
        self.assertFalse(quiet_now(settings, 22))
        settings.quiet_hours_start, settings.quiet_hours_end = 23, 7
        self.assertTrue(quiet_now(settings, 1))
        self.assertFalse(quiet_now(settings, 12))


if __name__ == '__main__':
    unittest.main()
