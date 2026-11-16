"""Regressions for Windows ASGI startup and saved AI settings; no live services."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import HTTPException
import test_storage
from models import AISettings
from routers.settings import (
    SaveAISettingsRequest, TestAIRequest, api_save_ai_settings, api_test_ai_connection,
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
