import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from database import Database
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
        backup = await self.db.backup(self.root / "snapshot.db")
        restored = Database(backup)
        await restored.init_db()
        self.assertIsNotNone(await restored.get_item_by_id("backup"))
        with closing(sqlite3.connect(backup)) as connection:
            self.assertEqual(connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 3)
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


if __name__ == "__main__":
    unittest.main()
