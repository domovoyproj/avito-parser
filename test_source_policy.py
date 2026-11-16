import asyncio
import time
import unittest
from datetime import datetime, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from models import AvitoItem, ParseResult, SearchQuery
from source_policy import SourceCooldown, SourcePolicy, page_url, retry_seconds
from parser_core import AvitoDataExtractor
import test_storage
import http_engine
import browser_engine


class SourcePolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await test_storage.StorageTests.asyncSetUp(self)
        cookies = patch.object(http_engine.config.scraper, 'cookies_file', self.root / 'cookies.json')
        cookies.start()
        self.addCleanup(cookies.stop)

    async def test_both_engines_cap_unique_items_across_pages(self):
        from contextlib import ExitStack
        items = [AvitoItem(id=str(i), title='Fixture', url=f'https://www.avito.ru/fixture_{i}') for i in range(100)]
        for kind in ('http', 'browser'):
            for cap, pages in ((20, 1), (73, 2)):
                with self.subTest(kind=kind, cap=cap), ExitStack() as stack:
                    module = http_engine if kind == 'http' else browser_engine
                    stack.enter_context(patch.object(module, 'source_policy', AsyncMock()))
                    stack.enter_context(patch.object(module.asyncio, 'sleep', AsyncMock()))
                    batches = [items[:50], items[40:]]
                    if kind == 'http':
                        session = AsyncMock()
                        session.__aenter__.return_value = session
                        session.get.return_value = SimpleNamespace(status_code=200, text='fixture', headers={})
                        stack.enter_context(patch.object(module, 'AsyncSession', return_value=session))
                        stack.enter_context(patch.object(module.AvitoDataExtractor, 'extract_from_initial_data', side_effect=batches))
                        engine = module.AvitoHttpEngine()
                        fetch = session.get
                    else:
                        context = AsyncMock()
                        context.new_page.return_value.goto.return_value = SimpleNamespace(status=200)
                        engine = module.AvitoBrowserEngine(headless=True)
                        stack.enter_context(patch.object(engine, '_create_context', AsyncMock(return_value=context)))
                        stack.enter_context(patch.object(module, 'async_playwright', return_value=AsyncMock()))
                        stack.enter_context(patch.object(engine, '_check_and_handle_block', AsyncMock(return_value=False)))
                        stack.enter_context(patch.object(engine, '_emulate_human', AsyncMock()))
                        stack.enter_context(patch.object(engine, '_save_cookies', AsyncMock()))
                        stack.enter_context(patch.object(module, 'extract_page', side_effect=[ParseResult(items=b, outcome='success') for b in batches]))
                        fetch = context.new_page.return_value.goto
                    collected = AsyncMock()
                    result = await engine.parse_search('https://www.avito.ru/fixture', max_pages=50, max_items=cap, items_callback=collected)
                    self.assertEqual(fetch.await_count, pages)
                    self.assertEqual([item.id for item in result.items], [str(i) for i in range(cap)])
                    self.assertEqual(sum(len(call.args[0]) for call in collected.await_args_list), cap)
                    self.assertEqual(result.total_found, cap)
                    self.assertTrue(result.limit_reached)
                    self.assertFalse(result.exhaustive)

    async def test_monitor_keeps_first_page_when_later_page_is_blocked(self):
        import monitoring
        search = SearchQuery(name='Fixture', url='https://www.avito.ru/fixture', max_items=73)
        search.id = await self.db.add_search(search)
        item = AvitoItem(id='partial-monitor', title='Fixture', url=search.url)
        parsed = ParseResult(items=[item], outcome='blocked', retry_after_seconds=900, errors=['Blocked'])
        with patch.object(monitoring, 'db', self.db), \
                patch.object(monitoring.http_engine, 'parse_search', AsyncMock(return_value=parsed)) as parse, \
                patch.object(monitoring.browser_engine, 'parse_search', AsyncMock()) as browser:
            result = await monitoring.MonitoringService()._check_search(search)
        parse.assert_awaited_once_with(search.url, max_pages=50, max_items=73)
        browser.assert_not_awaited()
        self.assertEqual((result['outcome'], result['new'], result['retry_after_seconds']), ('blocked', 1, 900))
        self.assertIsNotNone(await self.db.get_item_by_id(item.id))

    async def test_pause_survives_restart_and_blocks_other_searches(self):
        policy = SourcePolicy(self.db)
        await policy.block('1200')
        restarted = SourcePolicy(self.db)
        self.assertGreater((await restarted.status())['retry_after_seconds'], 1190)
        with self.assertRaises(SourceCooldown):
            await restarted.wait()
        await restarted.success()
        self.assertTrue((await restarted.status())['paused'])
        await restarted.success(manual=True)
        self.assertFalse((await restarted.status())['paused'])

    async def test_waiting_request_cancels_without_reserving_future_slots(self):
        policy = SourcePolicy(self.db)
        await policy.wait()
        waiting = asyncio.create_task(SourcePolicy(self.db).wait())
        await asyncio.sleep(.05)
        self.assertFalse(waiting.done())
        await policy.block('600')
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiting
        with self.assertRaises(SourceCooldown):
            await policy.wait()

    async def test_concurrent_blocks_do_not_compound_backoff(self):
        policy = SourcePolicy(self.db)
        await asyncio.gather(policy.block(), SourcePolicy(self.db).block())
        self.assertEqual((await policy.status())['failures'], 1)
        async with self.db.connection() as connection:
            await connection.execute("UPDATE source_access_state SET blocked_until=0")
            await connection.commit()
        self.assertGreaterEqual(await policy.block(), 900)

    async def test_http_block_is_shared_and_never_retried(self):
        policy = SourcePolicy(self.db)
        session = AsyncMock()
        session.__aenter__.return_value = session
        session.get.return_value = SimpleNamespace(status_code=429, headers={'Retry-After':'1800'})
        with patch.object(http_engine, 'source_policy', policy), patch.object(http_engine, 'AsyncSession', return_value=session):
            result = await http_engine.AvitoHttpEngine().parse_search('https://www.avito.ru/fixture', max_pages=4)
            again = await http_engine.AvitoHttpEngine().parse_search('https://www.avito.ru/another')
        self.assertEqual((result.outcome, again.outcome), ('blocked', 'blocked'))
        self.assertGreaterEqual(result.retry_after_seconds, 1800)
        self.assertEqual(session.get.await_count, 1)

    async def test_repeated_pages_stop_and_pagination_has_one_page_parameter(self):
        session = AsyncMock()
        session.__aenter__.return_value = session
        session.get.return_value = SimpleNamespace(status_code=200, text='fixture', headers={})
        policy = AsyncMock()
        item = AvitoItem(id='fixture', title='Fixture', url='https://www.avito.ru/fixture')
        with patch.object(http_engine, 'source_policy', policy), patch.object(http_engine, 'AsyncSession', return_value=session), \
             patch.object(http_engine.AvitoDataExtractor, 'extract_from_initial_data', return_value=[item]), \
             patch.object(http_engine.asyncio, 'sleep', new_callable=AsyncMock):
            result = await http_engine.AvitoHttpEngine().parse_search('https://www.avito.ru/all?p=1&q=test#catalog', max_pages=10)
        self.assertEqual(session.get.await_count, 2)
        self.assertEqual(result.total_found, 1)
        self.assertEqual(session.get.await_args_list[-1].args[0], 'https://www.avito.ru/all?q=test&p=2#catalog')

    async def test_persistence_failure_does_not_repeat_network_request(self):
        session = AsyncMock()
        session.__aenter__.return_value = session
        session.get.return_value = SimpleNamespace(status_code=200, text='fixture', headers={})
        item = AvitoItem(id='fixture', title='Fixture', url='https://www.avito.ru/fixture')
        with patch.object(http_engine, 'source_policy', AsyncMock()), patch.object(http_engine, 'AsyncSession', return_value=session), \
             patch.object(http_engine.AvitoDataExtractor, 'extract_from_initial_data', return_value=[item]):
            result = await http_engine.AvitoHttpEngine().parse_search('https://www.avito.ru/fixture', items_callback=AsyncMock(side_effect=OSError('fixture')))
        self.assertEqual(session.get.await_count, 1)
        self.assertEqual(result.outcome, 'partial')

    async def test_browser_http_block_sets_shared_pause_and_closes_browser(self):
        context = AsyncMock()
        page = context.new_page.return_value
        page.goto.return_value = SimpleNamespace(status=429, headers={'retry-after':'900'})
        policy = AsyncMock()
        policy.block.return_value = 900
        engine = browser_engine.AvitoBrowserEngine(headless=True)
        with patch.object(engine,'_create_context',AsyncMock(return_value=context)), \
             patch.object(browser_engine,'async_playwright',return_value=AsyncMock()), \
             patch.object(browser_engine,'source_policy',policy):
            result=await engine.parse_search('https://www.avito.ru/fixture',max_pages=3)
        self.assertEqual(result.outcome,'blocked')
        self.assertEqual(result.retry_after_seconds,900)
        policy.block.assert_awaited_once_with('900')
        self.assertEqual(page.goto.await_count,1)
        context.browser.close.assert_awaited_once()

    async def test_browser_cancellation_closes_resources(self):
        context = AsyncMock()
        context.new_page.return_value.goto.side_effect=asyncio.CancelledError()
        engine = browser_engine.AvitoBrowserEngine(headless=True)
        with patch.object(engine,'_create_context',AsyncMock(return_value=context)), \
             patch.object(browser_engine,'async_playwright',return_value=AsyncMock()), \
             patch.object(browser_engine,'source_policy',AsyncMock()):
            with self.assertRaises(asyncio.CancelledError):
                await engine.parse_search('https://www.avito.ru/fixture')
        context.browser.close.assert_awaited_once()

    def test_retry_after_dates_invalid_and_extreme_values(self):
        date = format_datetime(datetime.fromtimestamp(time.time() + 600, timezone.utc), usegmt=True)
        self.assertTrue(598 <= retry_seconds(date) <= 601)
        self.assertEqual(retry_seconds('nan'), 300)
        self.assertEqual(retry_seconds('bad'), 300)
        self.assertEqual(retry_seconds('inf'), 300)
        self.assertEqual(retry_seconds('9999999'), 86400)
        self.assertEqual(retry_seconds('0'), 1)
        self.assertEqual(page_url('https://www.avito.ru/all?p=1&p=9&q=x',3), 'https://www.avito.ru/all?q=x&p=3')

    def test_price_does_not_merge_payments_quantities_or_kopeks(self):
        for value, price in [('15 500 ₽ / 2 шт.',15500),('15\u202f500,90 ₽',15500),('15000 ₽ или 500 ₽/мес.',15000),('Бесплатно',0),('Договорная',None)]:
            with self.subTest(value=value):
                self.assertEqual(AvitoDataExtractor.clean_price(value), price)


if __name__ == '__main__':
    unittest.main()
