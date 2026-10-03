import asyncio
import test_storage
import unittest
from unittest.mock import patch
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
