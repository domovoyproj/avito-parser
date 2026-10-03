import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from database import Database, SCHEMA_VERSION
from models import AvitoItem, SearchQuery


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "test.db")
        with patch.dict("os.environ", {}, clear=True):
            await self.db.init_db()

    async def test_concurrent_writes_history_and_memberships(self):
        first = await self.db.add_search(SearchQuery(name="first", url="https://www.avito.ru/first"))
        second = await self.db.add_search(SearchQuery(name="second", url="https://www.avito.ru/second"))
        await self.db.save_item(AvitoItem(id="shared", title="Shared", price=100, url="https://www.avito.ru/shared", search_query_id=first))
        results = await asyncio.gather(*(self.db.save_item(AvitoItem(id="shared", title="Shared", price=price,
                         url="https://www.avito.ru/shared", search_query_id=second)) for price in range(90, 95)))
        self.assertTrue(all(not result[1] for result in results))
        for search_id in (first, second):
            items, count = await self.db.get_items_filtered(search_query_id=search_id)
            self.assertEqual(count, 1)
            self.assertEqual(items[0].id, "shared")
        self.assertEqual([search['items_count'] for search in await self.db.get_searches_with_counts()], [1, 1])
        self.assertEqual(await self.db.cleanup_stale_items(first,['different']),0)
        self.assertFalse((await self.db.get_item_by_id('shared')).is_closed)
        async with self.db.connection() as connection:
            self.assertEqual((await (await connection.execute("PRAGMA foreign_keys")).fetchone())[0], 1)
            history = await (await connection.execute("SELECT old_price, new_price FROM price_history ORDER BY id")).fetchall()
            self.assertEqual(len(history), 5)
            self.assertEqual(history[0][0], 100)
            for left, right in zip(history, history[1:]):
                self.assertEqual(left[1], right[0])
            self.assertEqual(await (await connection.execute("PRAGMA foreign_key_check")).fetchall(), [])

    async def test_backup_upgrade_and_restore(self):
        await self.db.save_item(AvitoItem(id="backup", title="Backup", price=100, url="https://www.avito.ru/backup"))
        started=asyncio.Event()
        async def writer():
            for index in range(10):
                await self.db.save_item(AvitoItem(id=f'live-{index}',title='Live write',url='https://www.avito.ru/fixture'))
                started.set()
                await asyncio.sleep(0)
        live=asyncio.create_task(writer())
        await started.wait()
        backup = await self.db.backup(self.root / "snapshot.db")
        await live
        restored = Database(backup)
        await restored.init_db()
        self.assertIsNotNone(await restored.get_item_by_id("backup"))
        with closing(sqlite3.connect(backup)) as connection:
            self.assertEqual(connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        with self.assertRaises(ValueError):
            await self.db.backup(self.db.db_path)

    async def test_transaction_rollback_and_delete_cascade(self):
        item = AvitoItem(id="rollback", title="Fixture", url="https://www.avito.ru/fixture", search_query_id=9999)
        with self.assertRaises(sqlite3.IntegrityError):
            await self.db.save_item(item)
        self.assertIsNone(await self.db.get_item_by_id(item.id))

    async def test_even_median_excludes_closed_and_zero(self):
        search = await self.db.add_search(SearchQuery(name='market', url='https://www.avito.ru/market'))
        for index, (price, closed) in enumerate([(100, False), (200, False), (1000000, True), (0, False)]):
            await self.db.save_item(AvitoItem(id=f'median-{index}', title='Fixture', price=price,
                                           is_closed=closed, url='https://www.avito.ru/fixture', search_query_id=search))
        self.assertEqual(await self.db.get_search_market_stats(search), (150, 150))

    async def test_batch_scoring_uses_one_market_snapshot_and_persists_factors(self):
        from unittest.mock import AsyncMock
        search=await self.db.add_search(SearchQuery(name='batch',url='https://www.avito.ru/fixture'))
        items=[AvitoItem(id=f'batch-{index}',title='Fixture',price=100,url='https://www.avito.ru/fixture',search_query_id=search) for index in range(3)]
        with patch.object(self.db,'get_search_market_stats',AsyncMock(return_value=(200,200))) as market:
            await self.db.save_items(items)
            market.assert_awaited_once_with(search)
        saved=await self.db.get_item_by_id('batch-0')
        self.assertEqual(saved.score_version,'deterministic-2')
        self.assertTrue(0 <= saved.score_confidence <= 1)
        self.assertTrue(0 <= saved.deal_score <= 100)
        self.assertTrue(saved.deal_reasons)

    async def test_future_schema_refused_before_ddl(self):
        future = self.root / 'future.db'
        with closing(sqlite3.connect(future)) as connection:
            connection.execute('CREATE TABLE schema_migrations (version INTEGER)')
            connection.execute('INSERT INTO schema_migrations VALUES (99)')
            connection.commit()
        with self.assertRaises(ValueError):
            await Database(future).init_db()
        with closing(sqlite3.connect(future)) as connection:
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [('schema_migrations',)])

    async def test_restore_drill_preserves_previous_database_and_rejects_invalid_snapshot(self):
        from manage_db import restore_database
        await self.db.save_item(AvitoItem(id='before',title='Before',url='https://www.avito.ru/before'))
        snapshot = await self.db.backup(self.root/'snapshot.db')
        await self.db.save_item(AvitoItem(id='after',title='After',url='https://www.avito.ru/after'))
        await restore_database(snapshot,self.db.db_path)
        await self.db.init_db()
        self.assertIsNotNone(await self.db.get_item_by_id('before'))
        self.assertIsNone(await self.db.get_item_by_id('after'))
        self.assertEqual(len(list((self.root/'backups').glob('*.db'))),1)
        broken = self.root/'broken.db'; broken.write_bytes(b'not sqlite')
        with self.assertRaises(sqlite3.DatabaseError):
            await restore_database(broken,self.db.db_path)
        self.assertIsNotNone(await self.db.get_item_by_id('before'))

    async def test_actual_legacy_schema_migrates_without_losing_data(self):
        legacy = self.root/'legacy.db'
        script = (Path(__file__).parent/'tests/fixtures/legacy_database.sql').read_text(encoding='utf-8')
        with closing(sqlite3.connect(legacy)) as connection:
            connection.executescript(script)
        database = Database(legacy)
        await database.init_db()
        await database.init_db()
        item = await database.get_item_by_id('legacy')
        self.assertEqual((item.title,item.price,item.score_version), ('Legacy item',100,'legacy-1'))
        items,count = await database.get_items_filtered(search_query_id='1')
        self.assertEqual(count,1)
        self.assertEqual(items[0].id,'legacy')
        async with database.connection() as connection:
            self.assertEqual(await (await connection.execute('PRAGMA foreign_key_check')).fetchall(), [])


if __name__ == "__main__":
    unittest.main()
