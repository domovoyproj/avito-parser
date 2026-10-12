"""Access challenges and long jobs: isolated databases, no live Avito requests."""
import asyncio
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import test_config
from access_guard import access_challenge
from browser_engine import AvitoBrowserEngine
from database import db
from dependencies import get_browser_engine, get_parser_jobs, get_monitoring_service
from models import AvitoItem, ParseResult, SearchQuery
from monitoring import MonitoringService
from parser_jobs import ParsingJobManager
from routers import parser
import web_server


class AccessGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_alone_is_not_a_captcha(self):
        self.assertIsNone(access_challenge('<script src="https://smartcaptcha.yandexcloud.net/captcha.js"></script><h1>Телефоны</h1>'))
        self.assertIsNone(access_challenge('<div data-marker="item">Доступ ограничен, пройдите проверку</div><iframe aria-hidden="true" src="https://smartcaptcha.yandexcloud.net/check"></iframe>'))
        for html in ('<div data-marker="captcha">Проверка</div>', '<title>Captcha</title>',
                     '<iframe src="https://smartcaptcha.yandexcloud.net/check"></iframe>'):
            self.assertEqual(access_challenge(html), 'captcha')
        self.assertEqual(access_challenge('<h1>Доступ ограничен</h1>'), 'blocked')
        self.assertEqual(access_challenge('', 'https://www.avito.ru/validate_captcha'), 'captcha')

    async def test_manual_wait_checks_full_challenge_not_just_title(self):
        engine = AvitoBrowserEngine(headless=False)
        page = MagicMock()
        page.url = 'https://www.avito.ru/fixture'
        page.title = AsyncMock(return_value='Авито')
        page.content = AsyncMock(side_effect=['<div data-marker="captcha">Капча</div>',
                                             '<iframe src="https://smartcaptcha.yandexcloud.net/check"></iframe>',
                                             '<p>Ничего не найдено</p>'])
        callback = AsyncMock()
        with patch('browser_engine.asyncio.sleep', AsyncMock()), patch.object(engine, '_save_cookies', AsyncMock()):
            self.assertFalse(await engine._check_and_handle_block(page, callback, {}, 2))
        self.assertEqual(callback.await_args_list[0].args[0]['status'], 'waiting_human')
        self.assertEqual(callback.await_args_list[-1].args[0]['status'], 'running')
        self.assertEqual(page.content.await_count, 3)

    async def test_repeated_challenge_stops_without_another_wait(self):
        page = MagicMock()
        page.url = 'https://www.avito.ru/fixture'
        page.title = AsyncMock(return_value='Captcha')
        page.content = AsyncMock(return_value='<title>Captcha</title>')
        callback = AsyncMock()
        engine = AvitoBrowserEngine(headless=False)
        with patch('browser_engine.asyncio.sleep', AsyncMock()) as sleep:
            self.assertTrue(await engine._check_and_handle_block(page, callback, {'count': 2}))
            sleep.assert_not_awaited()
        self.assertTrue(callback.await_args.args[0]['repeated_captcha'])


class ParserJobTests(unittest.IsolatedAsyncioTestCase):
    setUp = test_config.ConfigTests.setUp

    async def asyncSetUp(self):
        await test_config.SettingsAPITests.asyncSetUp(self)
        self.manager = ParsingJobManager()
        self.monitor = MonitoringService()
        web_server.app.dependency_overrides[get_parser_jobs] = lambda: self.manager
        web_server.app.dependency_overrides[get_monitoring_service] = lambda: self.monitor
        self.addCleanup(web_server.app.dependency_overrides.pop, get_parser_jobs, None)
        self.addCleanup(web_server.app.dependency_overrides.pop, get_monitoring_service, None)
        self.addAsyncCleanup(self.cancel_remaining)

    async def cancel_remaining(self):
        tasks = list(self.manager.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def start(self, **options):
        response = await self.client.post('/api/parser/start', json={
            'url': 'https://www.avito.ru/fixture', 'engine': 'http', **options})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['job_id']

    async def completed(self, job_id):
        task = self.manager.tasks.get(job_id)
        if task:
            await asyncio.wait_for(asyncio.shield(task), 10)
        return (await self.client.get('/api/parser/jobs/' + job_id)).json()

    async def test_block_is_not_success_and_collected_page_is_saved(self):
        item = AvitoItem(id='partial-fixture', title='Fixture', price=100, url='https://www.avito.ru/fixture_1')

        async def parse(*args, items_callback, progress_callback, **kwargs):
            await items_callback([item])
            await progress_callback(1, 3, 1)
            return ParseResult(items=[item], outcome='blocked', errors=['Источник ограничил доступ'])

        with patch.object(web_server.http_engine, 'parse_search', side_effect=parse):
            job = await self.completed(await self.start())
        self.assertEqual((job['status'], job['outcome'], job['saved_count']), ('blocked', 'blocked', 1))
        self.assertEqual(job['new_items_count'], 1)
        self.assertEqual((await db.get_item_by_id(item.id)).title, 'Fixture')
        attention = (await self.client.get('/api/parser/attention')).json()
        self.assertEqual(attention['jobs'][0]['job_id'], job['job_id'])
        await self.client.post('/api/parser/jobs/' + job['job_id'] + '/dismiss')
        self.assertEqual((await self.client.get('/api/parser/attention')).json()['jobs'], [])

    async def test_empty_is_explicit_and_job_snapshot_requires_authentication(self):
        with patch.object(web_server.http_engine, 'parse_search', AsyncMock(return_value=ParseResult(outcome='empty'))):
            job = await self.completed(await self.start())
        self.assertEqual((job['status'], job['outcome']), ('completed', 'empty'))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web_server.app), base_url='http://test') as anonymous:
            self.assertEqual((await anonymous.get('/api/parser/jobs/' + job['job_id'])).status_code, 401)
        self.assertEqual((await self.client.get('/api/parser/jobs/missing')).status_code, 404)

    async def test_timeout_keeps_page_and_reports_actionable_error(self):
        ready = asyncio.Event()
        item = AvitoItem(id='timeout-fixture', title='Fixture', url='https://www.avito.ru/fixture_2')

        async def parse(*args, items_callback, **kwargs):
            await items_callback([item])
            ready.set()
            await asyncio.Event().wait()

        with patch.object(web_server.http_engine, 'parse_search', side_effect=parse), patch.object(parser, 'job_timeout_seconds', return_value=3):
            job_id = await self.start()
            await asyncio.wait_for(ready.wait(), 5)
            job = await self.completed(job_id)
        self.assertEqual((job['status'], job['outcome'], job['saved_count']), ('error', 'timeout', 1))
        self.assertIsNotNone(await db.get_item_by_id(item.id))
        self.assertIn('Истекло время', job['errors'][0])

    async def test_cancel_keeps_page_and_terminal_state(self):
        ready = asyncio.Event()
        item = AvitoItem(id='cancel-fixture', title='Fixture', url='https://www.avito.ru/fixture_3')

        async def parse(*args, items_callback, **kwargs):
            await items_callback([item])
            ready.set()
            await asyncio.Event().wait()

        with patch.object(web_server.http_engine, 'parse_search', side_effect=parse):
            job_id = await self.start()
            await asyncio.wait_for(ready.wait(), 5)
            self.assertEqual((await self.client.post('/api/parser/' + job_id + '/cancel')).status_code, 200)
        job = (await self.client.get('/api/parser/jobs/' + job_id)).json()
        self.assertEqual((job['status'], job['outcome'], job['saved_count']), ('cancelled', 'cancelled', 1))
        self.assertIsNotNone(await db.get_item_by_id(item.id))

    async def test_human_retry_opens_headed_engine_once_and_reports_wait(self):
        with patch.object(web_server.http_engine, 'parse_search', AsyncMock(return_value=ParseResult(outcome='blocked'))):
            old = await self.completed(await self.start())
        waiting = asyncio.Event()

        class FixtureBrowser:
            def __init__(self, headless=True):
                self.headless = headless

            async def parse_search(instance, url, access_callback, **kwargs):
                self.assertFalse(instance.headless)
                await access_callback({'status': 'waiting_human', 'access_kind': 'captcha', 'access_message': 'Fixture check'})
                waiting.set()
                await asyncio.Event().wait()

        web_server.app.dependency_overrides[get_browser_engine] = FixtureBrowser
        self.addCleanup(web_server.app.dependency_overrides.pop, get_browser_engine, None)
        response = await self.client.post('/api/parser/jobs/' + old['job_id'] + '/retry-human')
        new_id = response.json()['job_id']
        await asyncio.wait_for(waiting.wait(), 5)
        same = await self.client.post('/api/parser/jobs/' + old['job_id'] + '/retry-human')
        self.assertEqual(same.json()['job_id'], new_id)
        job = (await self.client.get('/api/parser/jobs/' + new_id)).json()
        self.assertEqual(job['status'], 'waiting_human')
        self.assertFalse(job['headless'])
        await self.client.post('/api/parser/' + new_id + '/cancel')

    async def test_monitoring_circuit_breaker_escalates_and_clears_on_manual_success(self):
        search_id = await db.add_search(SearchQuery(name='Fixture', url='https://www.avito.ru/fixture'))
        search = await db.get_search_by_id(search_id)
        with patch.object(self.monitor, '_check_search', AsyncMock(return_value={'new':0, 'drops':0, 'errors':1, 'outcome':'blocked'})) as check:
            for count, seconds in ((1,300),(2,900),(3,3600)):
                if count > 1:
                    self.monitor.access_alerts[search_id]['retry_at'] = 0
                self.assertEqual((await self.monitor.check_search(search))['outcome'], 'blocked')
                self.assertEqual(self.monitor.access_alerts[search_id]['count'], count)
                self.assertGreater(self.monitor.access_alerts[search_id]['retry_at'] - time.time(), seconds-5)
            self.assertEqual((await self.monitor.check_search(search))['error_type'], 'AccessCooldown')
            self.assertEqual(check.await_count, 3)
        self.assertEqual(len(await db.get_monitoring_runs(search_id=search_id)), 4)
        with patch.object(web_server.http_engine, 'parse_search', AsyncMock(return_value=ParseResult(outcome='empty'))):
            await self.completed(await self.start())
        self.assertEqual(self.monitor.access_alerts, {})


if __name__ == '__main__':
    unittest.main()
