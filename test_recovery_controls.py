import asyncio
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from browser_session import CookieStore, DetailParseError
from browser_engine import AvitoBrowserEngine
from models import AvitoItem
from parse_outcomes import extract_detail
from config import config
from database import db
import test_config
import test_storage


class SessionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'cookies.json'
        self.store = CookieStore(self.path)

    def test_legacy_reset_and_stale_writer_cannot_restore_cookies(self):
        self.path.write_text(json.dumps([{'name':'session', 'value':'old'}]), encoding='utf-8')
        self.assertEqual(self.store.snapshot()['cookies'][0]['value'], 'old')
        generation = self.store.reset('legacy')
        self.assertTrue(self.store.snapshot()['native_user_agent'])
        self.assertEqual(self.store.snapshot()['cookies'], [])
        self.assertFalse(self.store.save([{'name':'session', 'value':'stale'}], 'legacy'))
        self.assertTrue(self.store.save([{'name':'session', 'value':'solved'}], generation))
        self.assertIsNone(self.store.reset('legacy'))
        self.assertEqual(self.store.snapshot()['cookies'][0]['value'], 'solved')

    async def test_fresh_browser_uses_native_agent_and_empty_cookies(self):
        self.store.reset()
        engine = AvitoBrowserEngine(headless=True)
        engine.cookies_file = self.path
        context = MagicMock(add_init_script=AsyncMock(), add_cookies=AsyncMock())
        browser = MagicMock(new_context=AsyncMock(return_value=context))
        playwright = MagicMock()
        playwright.chromium.launch = AsyncMock(return_value=browser)
        await engine._create_context(playwright)
        self.assertNotIn('user_agent', browser.new_context.call_args.kwargs)
        context.add_cookies.assert_not_awaited()
        import http_engine
        http = http_engine.AvitoHttpEngine()
        http.cookies_file = self.path
        self.assertNotIn('User-Agent', http._get_headers())
        self.assertNotIn('Sec-Ch-Ua', http._get_headers())

    async def test_detail_statuses_do_not_save_error_pages(self):
        import browser_engine
        for status, expected in ((404,404), (403,429), (500,502), (200,422)):
            with self.subTest(status=status):
                engine = AvitoBrowserEngine(headless=True)
                engine.cookies_file = self.path
                context = AsyncMock()
                context._avito_generation = self.store.snapshot()['generation']
                page = context.new_page.return_value
                page.goto.return_value = SimpleNamespace(status=status, headers={})
                page.content.return_value = '<h1>Temporary error</h1>'
                with patch.object(engine, '_create_context', AsyncMock(return_value=context)), \
                        patch.object(engine, '_check_and_handle_block', AsyncMock(return_value=False)), \
                        patch.object(engine, '_emulate_human', AsyncMock()), \
                        patch.object(browser_engine, 'async_playwright', return_value=AsyncMock()), \
                        patch.object(browser_engine, 'source_policy', AsyncMock()), \
                        patch.object(browser_engine.asyncio, 'sleep', AsyncMock()):
                    with self.assertRaises(DetailParseError) as raised:
                        await engine.parse_item_detail('https://www.avito.ru/item_123', raise_errors=True)
                self.assertEqual(raised.exception.status_code, expected)
                context.browser.close.assert_awaited_once()

    async def test_detail_captcha_accepts_real_detail_not_only_search_results(self):
        import browser_engine
        html = '<h1 data-marker="item-view/title-info">iPhone</h1><span itemprop="price">12000 ₽</span>'
        self.assertIsNone(extract_detail('<h1>Temporary error</h1>', 'https://www.avito.ru/item_123'))
        self.assertEqual(extract_detail(html, 'https://www.avito.ru/item_123').title, 'iPhone')
        engine = AvitoBrowserEngine(headless=False)
        engine.cookies_file = self.path
        page = MagicMock()
        page.url = 'https://www.avito.ru/item_123'
        page.context._avito_generation = 'legacy'
        page.title = AsyncMock(return_value='Avito')
        page.content = AsyncMock(side_effect=['<div data-marker="captcha">Captcha</div>', html])
        with patch.object(browser_engine.asyncio, 'sleep', AsyncMock()), patch.object(engine, '_save_cookies', AsyncMock()) as save:
            self.assertFalse(await engine._check_and_handle_block(page, detail_url=page.url))
        save.assert_awaited_once()


class DropResetTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_storage.StorageTests.asyncSetUp

    async def test_selected_events_are_personal_and_history_is_retained(self):
        await self.db.bootstrap_admin('admin', 'fixture-password-2026')
        user = (await self.db.get_all_users())[0].id
        for price in (100,90,80):
            await self.db.save_item(AvitoItem(id='drop', title='Fixture', price=price, url='https://www.avito.ru/item_123'))
        original = await self.db.get_price_drops(user_id=user)
        self.assertEqual(len(original), 2)
        event = original[0]['history_id']
        self.assertEqual(await self.db.dismiss_price_drops(user, [event, event, 99999]), 1)
        self.assertEqual(len(await self.db.get_price_drops(user_id=user)), 1)
        self.assertEqual(len(await self.db.get_price_drops()), 2)
        self.assertEqual(len(await self.db.get_price_history('drop')), 2)
        self.assertEqual(await self.db.dismiss_price_drops(user, [event]), 0)


class RecoveryAPITests(unittest.IsolatedAsyncioTestCase):
    setUp = test_config.SettingsAPITests.setUp
    asyncSetUp = test_config.SettingsAPITests.asyncSetUp

    async def test_drop_reset_api_and_detail_error_are_explicit(self):
        for price in (100, 90):
            await db.save_item(AvitoItem(id='api-drop',title='Fixture',price=price,url='https://www.avito.ru/item_123'))
        events = (await self.client.get('/api/price-drops')).json()
        response = await self.client.post('/api/price-drops/reset', json={'history_ids':[events[0]['history_id']]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((await self.client.get('/api/price-drops')).json(), [])
        self.assertEqual((await self.client.post('/api/price-drops/reset',json={'history_ids':[]})).status_code,422)
        import web_server
        with patch.object(web_server.browser_engine, 'parse_item_detail', AsyncMock(side_effect=DetailParseError(429,'Fixture block'))) as parse:
            response = await self.client.post('/api/parser/item',json={'url':'https://www.avito.ru/item_123'})
        self.assertEqual(response.status_code,429)
        self.assertEqual(response.json()['detail'],'Fixture block')
        parse.assert_awaited_once_with('https://www.avito.ru/item_123',manual=True,raise_errors=True)


class TelegramReceiverTests(unittest.IsolatedAsyncioTestCase):
    async def test_receiver_starts_once_and_closes_on_shutdown(self):
        import telegram_bot
        entered = asyncio.Event()
        @asynccontextmanager
        async def fake_lease(*args, **kwargs):
            yield True
        async def polling(*args, **kwargs):
            self.assertFalse(kwargs['handle_signals'])
            entered.set()
            await asyncio.Event().wait()
        bot = MagicMock()
        bot.session.close = AsyncMock()
        with patch.object(config.telegram,'bot_token','fixture-only'), \
                patch.object(telegram_bot,'lease',fake_lease), \
                patch.object(telegram_bot,'Bot',return_value=bot), \
                patch.object(telegram_bot.dp,'start_polling',side_effect=polling) as start:
            task = asyncio.create_task(telegram_bot.run_command_polling())
            await asyncio.wait_for(entered.wait(),2)
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        start.assert_awaited_once()
        bot.session.close.assert_awaited_once()
        self.assertEqual(telegram_bot.command_status['state'],'stopped')


class OperationalAlertTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_storage.StorageTests.asyncSetUp

    async def test_durable_dedup_delivery_and_history_without_product_filters(self):
        from notifications import queue_parser_alert
        from outbox import OutboxWorker
        import outbox
        response = MagicMock(status_code=200)
        response.json.return_value = {'ok':True}
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = response
        with patch.object(config.telegram,'bot_token','fixture-token'), patch.object(config.telegram,'admin_chat_ids',[123]):
            counts = await asyncio.gather(*(queue_parser_alert('access','https://www.avito.ru/fixture','CAPTCHA <test>',database=self.db) for _ in range(3)))
            self.assertEqual(sum(counts),1)
            worker = OutboxWorker(self.db)
            job = await worker.claim()
            with patch.object(outbox.httpx,'AsyncClient',return_value=client):
                await worker.deliver(job)
            client.post.assert_awaited_once()
            message = client.post.await_args.kwargs['json']
            self.assertEqual(message['chat_id'],123)
            self.assertIn('CAPTCHA &lt;test&gt;',message['text'])
            self.assertIn('руч',message['text'].lower())
            events = await self.db.get_notification_events()
            self.assertEqual(events['events'][0]['type'],'access')
            self.assertEqual(events['events'][0]['status'],'sent')
            self.assertEqual(await queue_parser_alert('parser_error','https://www.avito.ru/fixture','Timeout',database=self.db),1)

    async def test_telegram_429_retries_system_alert(self):
        from notifications import queue_parser_alert
        from outbox import OutboxWorker
        import outbox
        response = MagicMock(status_code=429)
        response.json.return_value = {'parameters':{'retry_after':120}}
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = response
        with patch.object(config.telegram,'bot_token','fixture-token'), patch.object(config.telegram,'admin_chat_ids',[123]):
            await queue_parser_alert('access','https://www.avito.ru/fixture','CAPTCHA',database=self.db)
            worker = OutboxWorker(self.db)
            with patch.object(outbox.httpx,'AsyncClient',return_value=client):
                await worker.deliver(await worker.claim())
            event = (await self.db.get_notification_events())['events'][0]
            self.assertEqual((event['status'],event['last_error']),('pending','HTTP 429'))
            self.assertIsNone(await worker.claim())
