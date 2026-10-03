import asyncio
import test_storage
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
import httpx
from ai_scoring import DealScoringEngine
from models import AISettings, AvitoItem
from database import db
from llm_store import cached_verdict, reserve_budget, save_verdict


class LLMStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await test_storage.StorageTests.asyncSetUp(self)
        context = patch.object(db, 'db_path', self.db.db_path)
        context.start()
        self.addCleanup(context.stop)

    async def test_shared_budget_atomic_and_cache_survives_init(self):
        results = await asyncio.gather(*(reserve_budget('fixture-day', limit=5) for _ in range(12)))
        self.assertEqual(sum(results), 5)
        await save_verdict('fixture-key', 'verdict')
        await self.db.init_db()
        self.assertEqual(await cached_verdict('fixture-key'), 'verdict')
        async with self.db.connection() as connection:
            await connection.execute('UPDATE llm_cache SET expires_at=0')
            await connection.commit()
        self.assertIsNone(await cached_verdict('fixture-key'))

    async def test_provider_missing_price_cache_invalidation_and_failures(self):
        client=MagicMock()
        client.__aenter__=AsyncMock(return_value=client)
        client.__aexit__=AsyncMock(return_value=False)
        client.post=AsyncMock(return_value=httpx.Response(200,json={'choices':[{'message':{'content':'Проверить товар'}}]}))
        settings=AISettings(enabled=True,api_key='fixture-key')
        item=AvitoItem(id='llm-fixture',title='Fixture',url='https://www.avito.ru/fixture',price=None)
        with patch('ai_scoring.httpx.AsyncClient',return_value=client):
            verdict=await DealScoringEngine.generate_ai_verdict(item,ai_settings=settings)
            self.assertEqual(verdict,'Проверить товар')
            self.assertIn('не указана',client.post.call_args.kwargs['json']['messages'][1]['content'])
            await DealScoringEngine.generate_ai_verdict(item,ai_settings=settings)
            self.assertEqual(client.post.await_count,1)
            item.description='Изменённое описание'
            client.post.return_value=httpx.Response(429,json={'error':'rate limit'})
            self.assertIsNone(await DealScoringEngine.generate_ai_verdict(item,ai_settings=settings))
            self.assertEqual(client.post.await_count,2)
            client.post.side_effect=httpx.ReadTimeout('fixture timeout')
            self.assertIsNone(await DealScoringEngine.generate_ai_verdict(item,ai_settings=settings))
            self.assertEqual(client.post.await_count,3)
