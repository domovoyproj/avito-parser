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
from datetime import datetime, timedelta, timezone


class MonitoringTests(unittest.IsolatedAsyncioTestCase):
    async def test_blacklisted_seller_never_reaches_delivery(self):
        from models import SellerInfo
        settings=config.config.telegram.model_copy(deep=True)
        settings.bot_token,settings.admin_chat_ids='fixture-only',[1]
        with patch.object(config.config,'telegram',settings):
            await self.db.save_item(AvitoItem(id='blocked-seller',title='Fixture',url='https://www.avito.ru/fixture',seller=SellerInfo(name='Blocked')))
        async with self.db.connection() as connection:
            await connection.execute("INSERT INTO seller_blacklist(seller_name) VALUES ('Blocked')")
            await connection.commit()
        worker=OutboxWorker(self.db)
        with patch('outbox.httpx.AsyncClient') as network:
            await worker.deliver(await worker.claim())
            network.assert_not_called()
        async with self.db.connection() as connection:
            self.assertEqual((await (await connection.execute('SELECT status FROM notification_outbox')).fetchone())[0],'skipped')

    async def test_notification_zero_price_long_text_and_unsafe_link(self):
        from models import SellerInfo
        from notifications import format_item_notification
        from bs4 import BeautifulSoup
        item=AvitoItem(id='text',title='<script>'*1000,price=0,old_price=100,url='javascript:alert(1)',
                      seller=SellerInfo(name='x'*10000),deal_reasons=['&'*1000]*20,detected_flaws=['<'*1000]*20,ai_summary='>'*10000)
        value=await format_item_notification(item,'Поиск'*1000,'price_drop')
        plain=BeautifulSoup(value,'html.parser').get_text()
        self.assertIn('0 ₽',plain)
        self.assertLess(len(plain),4096)
        self.assertNotIn('javascript:',value)
        self.assertNotIn('<script>',value)

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

    async def test_lost_lease_cancels_holder(self):
        started=asyncio.Event()
        async def hold():
            async with lease('lost',database=self.db,ttl=1,renewal_interval=0.05) as acquired:
                self.assertTrue(acquired)
                started.set()
                await asyncio.Event().wait()
        task=asyncio.create_task(hold())
        await started.wait()
        async with self.db.connection() as connection:
            await connection.execute("DELETE FROM work_leases WHERE name='lost'")
            await connection.commit()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task,timeout=2)

    async def test_due_intervals_and_empty_results_do_not_load_browser(self):
        service=MonitoringService()
        now=datetime.now(timezone.utc)
        searches=[SearchQuery(id=1,name='stale',url='https://www.avito.ru/fixture',last_checked_at=now-timedelta(minutes=20)),
                  SearchQuery(id=2,name='fresh',url='https://www.avito.ru/fixture',last_checked_at=now),
                  SearchQuery(id=3,name='never',url='https://www.avito.ru/fixture')]
        with patch.object(db,'get_searches',AsyncMock(return_value=searches)), patch.object(service,'check_search',AsyncMock(return_value={'new':0,'drops':0})) as check:
            await service._run_all_now(due_only=True)
            self.assertEqual([call.args[0].id for call in check.await_args_list],[1,3])
        search_id=await db.add_search(SearchQuery(name='empty',url='https://www.avito.ru/fixture'))
        search=await db.get_search_by_id(search_id)
        with patch.object(monitoring.http_engine,'parse_search',AsyncMock(return_value=ParseResult(outcome='empty'))), patch.object(monitoring.browser_engine,'parse_search',AsyncMock()) as browser:
            await service.check_search(search)
            browser.assert_not_awaited()

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
        self.assertIsNotNone((await self.db.get_search_by_id(search.id)).last_checked_at)

    async def test_monitoring_history_survives_restart_filters_and_is_idempotent(self):
        search_id = await self.db.add_search(SearchQuery(name='История', url='https://www.avito.ru/fixture'))
        search = await self.db.get_search_by_id(search_id)
        service = MonitoringService()
        with patch.object(monitoring.http_engine, 'parse_search', AsyncMock(return_value=ParseResult(outcome='empty'))):
            self.assertEqual((await service.check_search(search))['outcome'], 'empty')
        runs = await self.db.get_monitoring_runs(search_id=search_id, outcome='empty')
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]['engine'], 'http')
        self.assertEqual(runs[0]['search_name'], 'История')
        self.assertEqual((await self.db.get_monitoring_run_stats())['outcomes']['empty'], 1)
        await self.db.record_monitoring_run(runs[0])
        self.assertEqual(len(await self.db.get_monitoring_runs()), 1)
        from database import Database
        restarted = Database(self.db.db_path)
        self.assertEqual((await restarted.get_monitoring_runs())[0]['run_id'], runs[0]['run_id'])

    async def test_monitoring_history_records_blocked_and_does_not_close_items(self):
        search_id = await self.db.add_search(SearchQuery(name='Blocked', url='https://www.avito.ru/fixture'))
        search = await self.db.get_search_by_id(search_id)
        with patch.object(monitoring.http_engine, 'parse_search', AsyncMock(return_value=ParseResult(outcome='blocked'))), \
             patch.object(db, 'cleanup_stale_items', AsyncMock()) as cleanup:
            result = await MonitoringService().check_search(search)
            cleanup.assert_not_awaited()
        self.assertEqual(result['outcome'], 'blocked')
        self.assertEqual((await self.db.get_monitoring_runs(outcome='blocked'))[0]['search_id'], search_id)

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
            rows = await (await connection.execute('SELECT id, status, attempts FROM notification_outbox')).fetchall()
        statuses = {row[0]: (row[1], row[2]) for row in rows}
        self.assertEqual(statuses[one['id']], ('pending', 1))
        self.assertEqual(statuses[two['id']], ('sent', 1))

    def test_quiet_hours_day_and_night(self):
        settings = TelegramChatSettings(chat_id=1, quiet_hours_enabled=True, quiet_hours_start=9, quiet_hours_end=17)
        self.assertTrue(quiet_now(settings, 12))
        self.assertFalse(quiet_now(settings, 22))
        settings.quiet_hours_start, settings.quiet_hours_end = 23, 7
        self.assertTrue(quiet_now(settings, 1))
        self.assertFalse(quiet_now(settings, 12))

    async def test_delivery_crash_lease_recovery_and_quiet_deferral(self):
        settings = config.config.telegram.model_copy(deep=True)
        settings.bot_token, settings.admin_chat_ids = 'fixture-only', [1]
        with patch.object(config.config, 'telegram', settings):
            await self.db.save_item(AvitoItem(id='recover', title='fixture', url='https://www.avito.ru/fixture'))
        worker = OutboxWorker(self.db)
        crashed = await worker.claim()
        async with self.db.connection() as connection:
            await connection.execute('UPDATE notification_outbox SET lease_until=0 WHERE id=?', (crashed['id'],))
            await connection.commit()
        recovered = await worker.claim()
        self.assertEqual(crashed['id'], recovered['id'])
        self.assertNotEqual(crashed['lease_token'], recovered['lease_token'])
        await worker.finish(crashed, 'sent')
        await worker.finish(recovered, 'pending', 300, 'quiet hours', attempted=False)
        async with self.db.connection() as connection:
            self.assertEqual(await (await connection.execute('SELECT status, attempts FROM notification_outbox')).fetchone(), ('pending', 0))
        self.assertIsNone(await worker.claim())

    async def test_invalid_payload_is_failed_instead_of_retried_forever(self):
        async with self.db.connection() as connection:
            await connection.execute("INSERT INTO notification_outbox(event_key,channel,destination,payload) VALUES ('bad','telegram','1','broken json')")
            await connection.commit()
        worker=OutboxWorker(self.db)
        await worker.deliver(await worker.claim())
        self.assertIsNone(await worker.claim())
        async with self.db.connection() as connection:
            self.assertEqual(await (await connection.execute('SELECT status,last_error FROM notification_outbox')).fetchone(),('failed','invalid payload'))


if __name__ == '__main__':
    unittest.main()
