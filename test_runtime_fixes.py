"""Regressions for Windows ASGI startup and saved AI settings; no live services."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from browser_engine import AvitoBrowserEngine
import browser_window
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import HTTPException
import test_storage
from models import AISettings
from ai_scoring import DealScoringEngine
from models import AvitoItem
from models import SearchQuery
from pydantic import ValidationError
from routers.searches import SearchCreateRequest, api_update_search
from routers.settings import (
    SaveAISettingsRequest, TestAIRequest, api_save_ai_settings, api_test_ai_connection,
    CustomRuleCreateRequest, api_add_scoring_rule,
)


class ConsoleStartupTests(unittest.TestCase):
    def test_asgi_and_standalone_engines_with_redirected_cp1251(self):
        root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, PYTHONIOENCODING='cp1251', PYTHONUTF8='0',
                       APP_ENV_FILE=str(Path(tmp) / 'absent.env'), PYTHONPATH=str(root))
            # Importing web_server:app is how uvicorn starts; run_server() is not called.
            for module in ('web_server', 'browser_engine', 'http_engine', 'main'):
                with self.subTest(module=module):
                    code = (f'import {module}; import sys; '
                            'from browser_engine import console; '
                            'console.print("✓ Страница: найдено 5 объявлений 🔎", markup=False); '
                            'sys.stderr.write("✓ Проверка ошибок\\n")')
                    result = subprocess.run([sys.executable, '-B', '-c', code],
                                            cwd=tmp, env=env, capture_output=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
                    self.assertIn('✓ Страница', result.stdout.decode('utf-8'))
                    self.assertIn('✓ Проверка ошибок', result.stderr.decode('utf-8'))


class AISettingsRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await test_storage.StorageTests.asyncSetUp(self)
        await self.db.save_ai_settings(AISettings(enabled=True, provider='openai',
            model='gpt-4o-mini', api_base='https://api.openai.com/v1',
            api_key='fixture-secret', prompt_template='Оцени iPhone без выдуманной маржи'))

    async def test_form_save_preserves_prompt_and_key_but_explicit_clear_works(self):
        values = dict(enabled=True, provider='openai', model='gpt-4o-mini',
                      api_base='https://api.openai.com/v1', api_key='')
        result = await api_save_ai_settings(SaveAISettingsRequest(**values), db=self.db)
        saved = await self.db.get_ai_settings()
        self.assertEqual(saved.prompt_template, 'Оцени iPhone без выдуманной маржи')
        self.assertEqual(saved.api_key, 'fixture-secret')
        self.assertEqual(result['settings']['api_key'], '')
        self.assertTrue(result['settings']['has_api_key'])
        await api_save_ai_settings(SaveAISettingsRequest(**values, prompt_template=None,
                                                       clear_api_key=True), db=self.db)
        saved = await self.db.get_ai_settings()
        self.assertIsNone(saved.prompt_template)
        self.assertEqual(saved.api_key, '')

    def client(self):
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock()
        return client

    def request(self):
        return TestAIRequest(provider='openai', model='gpt-4o-mini',
                             api_base='https://api.openai.com/v1', api_key='')

    async def test_connection_uses_saved_key_when_input_empty(self):
        client = self.client()
        client.post.return_value = httpx.Response(200, json={'choices':[{'message':{'content':'Связь работает'}}]})
        with patch('routers.settings.httpx.AsyncClient', return_value=client):
            result = await api_test_ai_connection(self.request(), db=self.db)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(client.post.call_args.kwargs['headers']['Authorization'], 'Bearer fixture-secret')
        self.assertNotIn('fixture-secret', str(result))

    async def test_network_errors_do_not_blame_key_or_expose_credentials(self):
        client = self.client()
        for error, status, phrase in (
            (httpx.ConnectError('fixture-secret'), 502, 'Ключ ещё не проверен'),
            (httpx.ReadTimeout('fixture-secret'), 504, '15 секунд'),
        ):
            with self.subTest(error=type(error).__name__):
                client.post.side_effect = error
                with patch('routers.settings.httpx.AsyncClient', return_value=client):
                    with self.assertRaises(HTTPException) as caught:
                        await api_test_ai_connection(self.request(), db=self.db)
                self.assertEqual(caught.exception.status_code, status)
                self.assertIn(phrase, caught.exception.detail)
                self.assertNotIn('fixture-secret', caught.exception.detail)

    async def test_provider_error_body_never_exposes_key(self):
        client = self.client()
        for status in (401, 403, 404, 429, 500):
            with self.subTest(status=status):
                client.post.return_value = httpx.Response(status, json={'error':{'message':'Invalid key fixture-secret'}})
                with patch('routers.settings.httpx.AsyncClient', return_value=client):
                    with self.assertRaises(HTTPException) as caught:
                        await api_test_ai_connection(self.request(), db=self.db)
                self.assertIn(str(status), caught.exception.detail)
                self.assertNotIn('fixture-secret', caught.exception.detail)

    async def test_missing_key_is_rejected_without_network_request(self):
        settings = await self.db.get_ai_settings()
        settings.api_key = ''
        await self.db.save_ai_settings(settings)
        with patch('routers.settings.httpx.AsyncClient') as client:
            with self.assertRaises(HTTPException) as caught:
                await api_test_ai_connection(self.request(), db=self.db)
            client.assert_not_called()
        self.assertEqual(caught.exception.status_code, 400)

    async def test_invalid_rule_is_not_saved(self):
        with self.assertRaises(HTTPException) as caught:
            await api_add_scoring_rule(CustomRuleCreateRequest(pattern='(', label='Broken', score_delta=-7), db=self.db)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(await self.db.get_custom_scoring_rules(active_only=False), [])


class CustomRuleRegressionTests(unittest.TestCase):
    def setUp(self):
        for name in ('_custom_penalties', '_custom_boosters'):
            context = patch.object(DealScoringEngine, name, [])
            context.start()
            self.addCleanup(context.stop)

    def test_custom_weights_zero_and_combined_builtin_defects(self):
        for text in ('Проверочный маркер', 'Проверочный маркер, не включается'):
            item = AvitoItem(id='rules-fixture', title=text, price=100,
                             url='https://www.avito.ru/fixture')
            DealScoringEngine.load_custom_rules([])
            baseline = DealScoringEngine.evaluate_item(item)[0]
            for delta in (-7, -35, 0, 12):
                with self.subTest(text=text, delta=delta):
                    DealScoringEngine.load_custom_rules([dict(pattern='маркер', label='Custom rule', score_delta=delta)])
                    score, _, reasons, flaws = DealScoringEngine.evaluate_item(item)
                    self.assertEqual(score, max(0, min(100, baseline + delta)))
                    self.assertEqual('Custom rule' in flaws, delta < 0)
                    self.assertEqual('Custom rule' in reasons, delta > 0)

    def test_legacy_invalid_regex_does_not_break_scoring(self):
        item = AvitoItem(id='rules-fixture', title='Маркер', price=100, url='https://www.avito.ru/fixture')
        baseline = DealScoringEngine.evaluate_item(item)[0]
        DealScoringEngine.load_custom_rules([
            dict(pattern='(', label='Bad rule', score_delta=-25),
            dict(pattern='маркер', label='Good rule', score_delta=-7),
        ])
        result = DealScoringEngine.evaluate_item(item)
        self.assertEqual(result[0], max(0, baseline - 7))
        self.assertEqual(result[3], ['Good rule'])


class SearchSettingsRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await test_storage.StorageTests.asyncSetUp(self)
        self.search_id = await self.db.add_search(SearchQuery(name='Fixture',
            url='https://www.avito.ru/fixture', enabled=False,
            active_hours_start=9, active_hours_end=19))

    async def test_edit_keeps_unrepresented_schedule_and_accepts_explicit_hours(self):
        payload = dict(name='Renamed', url='https://www.avito.ru/fixture', enabled=False, min_price=0)
        await api_update_search(self.search_id, SearchCreateRequest(**payload), self.db)
        saved = await self.db.get_search_by_id(self.search_id)
        self.assertEqual((saved.name, saved.min_price, saved.active_hours_start, saved.active_hours_end), ('Renamed', 0, 9, 19))
        await api_update_search(self.search_id, SearchCreateRequest(**payload, active_hours_start=0, active_hours_end=24), self.db)
        saved = await self.db.get_search_by_id(self.search_id)
        self.assertEqual((saved.active_hours_start, saved.active_hours_end), (0, 24))

    async def test_invalid_edited_url_and_price_range_do_not_persist(self):
        with self.assertRaises(HTTPException):
            await api_update_search(self.search_id, SearchCreateRequest(name='Bad', url='https://example.com/'), self.db)
        self.assertEqual((await self.db.get_search_by_id(self.search_id)).name, 'Fixture')
        for prices in (dict(min_price=-1), dict(min_price=20, max_price=10)):
            with self.assertRaises(ValidationError):
                SearchCreateRequest(name='Bad', url='https://www.avito.ru/fixture', **prices)


class BrowserWindowRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_visible_browser_uses_compact_resizable_viewport(self):
        context = MagicMock()
        context.add_init_script = AsyncMock()
        browser = MagicMock()
        browser.new_context = AsyncMock(return_value=context)
        playwright = MagicMock()
        playwright.chromium.launch = AsyncMock(return_value=browser)
        with tempfile.TemporaryDirectory() as tmp:
            for headless in (False, True):
                engine = AvitoBrowserEngine(headless=headless)
                engine.cookies_file = Path(tmp) / 'absent.json'
                await engine._create_context(playwright)
                options = browser.new_context.call_args.kwargs
                if headless:
                    self.assertEqual(options['viewport'], {'width':1920, 'height':1080})
                else:
                    self.assertTrue(options['no_viewport'])
                    self.assertNotIn('viewport', options)
                    self.assertIn('--window-size=520,640', playwright.chromium.launch.call_args.kwargs['args'])

    async def test_topmost_targets_only_browser_pid_and_detaches(self):
        page = MagicMock()
        session = MagicMock()
        session.send = AsyncMock(return_value={'processInfo':[
            {'type':'renderer', 'id':456}, {'type':'browser', 'id':123}]})
        session.detach = AsyncMock()
        page.context.browser.new_browser_cdp_session = AsyncMock(return_value=session)
        with patch.object(browser_window.sys, 'platform', 'win32'), \
                patch.object(browser_window, 'pin_windows', side_effect=[[], [789]]) as pin, \
                patch.object(browser_window.asyncio, 'sleep', AsyncMock()):
            self.assertTrue(await browser_window.prepare_visible_window(page))
        self.assertEqual([call.args for call in pin.call_args_list], [(123,), (123,)])
        session.detach.assert_awaited_once()
