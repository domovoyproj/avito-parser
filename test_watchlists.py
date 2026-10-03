import unittest
from unittest.mock import patch

import httpx

from config import config
from database import db
from models import AvitoItem, UserRole
import test_config
import test_storage
import web_server


class WatchlistStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await test_storage.StorageTests.asyncSetUp(self)
        context = patch.object(db, 'db_path', self.db.db_path)
        context.start()
        self.addCleanup(context.stop)

    async def test_legacy_favorites_migrate_once_and_users_are_isolated(self):
        await self.db.bootstrap_admin('admin', 'fixture-password-2026')
        _, _, second_id = await self.db.create_user('second', 'fixture-password-2026')
        admin_id = (await self.db.get_all_users())[0].id
        item = AvitoItem(id='legacy', title='Legacy', url='https://www.avito.ru/legacy')
        await self.db.save_item(item)
        await self.db.toggle_item_favorite('legacy', True)
        async with self.db.connection() as connection:
            await connection.execute('DELETE FROM schema_migrations WHERE version>=8')
            await connection.commit()
        await self.db.init_db()
        admin_items, admin_count = await self.db.get_items_filtered(favorites_only=True, user_id=admin_id)
        second_items, second_count = await self.db.get_items_filtered(favorites_only=True, user_id=second_id)
        self.assertEqual((admin_count, second_count), (1, 1))
        self.assertEqual(admin_items[0].id, second_items[0].id)
        self.assertFalse(await self.db.toggle_personal_favorite(second_id, 'legacy'))
        await self.db.init_db()
        self.assertEqual((await self.db.get_items_filtered(favorites_only=True, user_id=second_id))[1], 0)
        self.assertEqual((await self.db.get_items_filtered(favorites_only=True, user_id=admin_id))[1], 1)

    async def test_target_alert_is_per_chat_deduplicated_and_link_is_one_time(self):
        await self.db.bootstrap_admin('admin', 'fixture-password-2026')
        user_id = (await self.db.get_all_users())[0].id
        token = await self.db.issue_telegram_link(user_id)
        self.assertFalse(await self.db.redeem_telegram_link('invalid', 123))
        self.assertTrue(await self.db.redeem_telegram_link(token, 123))
        self.assertFalse(await self.db.redeem_telegram_link(token, 123))
        self.assertEqual(await self.db.list_telegram_chats(user_id), [123])
        settings = config.telegram.model_copy(deep=True)
        settings.bot_token, settings.admin_chat_ids = 'fixture-only', []
        with patch.object(config, 'telegram', settings):
            await self.db.save_item(AvitoItem(id='deal', title='Deal', price=200, url='https://www.avito.ru/deal'))
            watchlist_id = await self.db.default_watchlist_id(user_id)
            self.assertTrue(await self.db.set_watchlist_item(user_id, watchlist_id, 'deal', target_price=150, note='Проверить'))
            await self.db.save_item(AvitoItem(id='deal', title='Deal', price=140, url='https://www.avito.ru/deal'))
            await self.db.save_item(AvitoItem(id='deal', title='Deal', price=140, url='https://www.avito.ru/deal'))
        async with self.db.connection() as connection:
            rows = await (await connection.execute("SELECT event_key,destination,payload FROM notification_outbox WHERE event_key LIKE 'watch:%'")).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], '123')
        self.assertIn('"watch_target_price": 150', rows[0][2])
        self.assertTrue(await self.db.unlink_telegram_chat(user_id, 123))


class WatchlistAPITests(unittest.IsolatedAsyncioTestCase):
    setUp = test_config.SettingsAPITests.setUp
    asyncSetUp = test_config.SettingsAPITests.asyncSetUp

    async def test_ownership_and_viewer_write_denial(self):
        await db.save_item(AvitoItem(id='private', title='Private', url='https://www.avito.ru/private'))
        created = await self.client.post('/api/watchlists', json={'name': 'Admin list'})
        self.assertEqual(created.status_code, 200, created.text)
        watchlist_id = created.json()['id']
        added = await self.client.put(f'/api/watchlists/{watchlist_id}/items/private', json={'target_price': 100})
        self.assertEqual(added.status_code, 200)
        _, _, operator_id = await db.create_user('operator', 'fixture-password-2026', UserRole.OPERATOR)
        _, _, viewer_id = await db.create_user('viewer', 'fixture-password-2026', UserRole.VIEWER)
        self.assertIsNotNone(operator_id)
        self.assertIsNotNone(viewer_id)
        operator = httpx.AsyncClient(transport=httpx.ASGITransport(app=web_server.app), base_url='http://test')
        self.addAsyncCleanup(operator.aclose)
        login = await operator.post('/api/auth/login', json={'username': 'operator', 'password': 'fixture-password-2026'})
        self.assertEqual(login.status_code, 200)
        operator.headers['X-CSRF-Token'] = operator.cookies['avito_csrf']
        self.assertEqual((await operator.get(f'/api/watchlists/{watchlist_id}/items')).status_code, 404)
        self.assertEqual((await operator.delete(f'/api/watchlists/{watchlist_id}')).status_code, 404)
        own = await operator.get('/api/watchlists')
        self.assertEqual(len(own.json()), 1)
        viewer = httpx.AsyncClient(transport=httpx.ASGITransport(app=web_server.app), base_url='http://test')
        self.addAsyncCleanup(viewer.aclose)
        login = await viewer.post('/api/auth/login', json={'username': 'viewer', 'password': 'fixture-password-2026'})
        self.assertEqual(login.status_code, 200)
        viewer.headers['X-CSRF-Token'] = viewer.cookies['avito_csrf']
        self.assertEqual((await viewer.post('/api/watchlists', json={'name': 'Forbidden'})).status_code, 403)
