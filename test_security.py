import asyncio
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
import httpx
from models import UserRole
from database import Database, db
from security import LoginLimiter, same_origin
import test_config
import web_server
from dependencies import get_parser_jobs


class SecurityTests(unittest.IsolatedAsyncioTestCase):
    setUp = test_config.SettingsAPITests.setUp
    asyncSetUp = test_config.SettingsAPITests.asyncSetUp

    async def test_ai_key_mask_preserve_replace_and_delete(self):
        response = await self.client.post('/api/ai/settings', json={'api_key': 'fixture-private-key'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('fixture-private-key', response.text)
        response = await self.client.get('/api/ai/settings')
        self.assertTrue(response.json()['settings']['has_api_key'])
        await self.client.post('/api/ai/settings', json={'api_key': '', 'model': 'new-model'})
        self.assertEqual((await db.get_ai_settings()).api_key, 'fixture-private-key')
        await self.client.post('/api/ai/settings', json={'clear_api_key': True})
        self.assertEqual((await db.get_ai_settings()).api_key, '')

    async def test_known_legacy_admin_password_disabled_and_local_reset_available(self):
        password,salt=db.hash_password('admin123')
        async with db.connection() as connection:
            await connection.execute("UPDATE users SET password_hash=?,salt=? WHERE username='admin'",(password,salt))
            await connection.commit()
        await db.init_default_admin()
        self.assertIsNone(await db.authenticate_user('admin','admin123'))
        users=await db.get_all_users()
        user=next(user for user in users if user.username=='admin')
        self.assertFalse(user.is_active)
        ok,_=await db.update_user(user.id,new_password='recovery-fixture-password',is_active=True,role=UserRole.ADMIN)
        self.assertTrue(ok)
        self.assertIsNotNone(await db.authenticate_user('admin','recovery-fixture-password'))

    async def test_parser_limits_cancel_and_spoofed_url(self):
        async def blocked(*args, **kwargs):
            await asyncio.Event().wait()
        manager = web_server.ParsingJobManager()
        web_server.app.dependency_overrides[get_parser_jobs]=lambda:manager
        with patch.object(web_server, 'job_manager', manager), patch.object(web_server.http_engine, 'parse_search', side_effect=blocked):
            try:
                self.assertEqual((await self.client.post('/api/parser/start', json={'url': 'https://attacker.test/avito.ru'})).status_code, 400)
                jobs = []
                for _ in range(3):
                    response = await self.client.post('/api/parser/start', json={'url': 'https://www.avito.ru/fixture', 'engine': 'http'})
                    self.assertEqual(response.status_code, 200)
                    jobs.append(response.json()['job_id'])
                self.assertEqual((await self.client.post('/api/parser/start', json={'url': 'https://www.avito.ru/fixture'})).status_code, 429)
                response = await self.client.post(f'/api/parser/{jobs[0]}/cancel')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(manager.active_jobs[jobs[0]]['status'], 'cancelled')
            finally:
                web_server.app.dependency_overrides.pop(get_parser_jobs,None)
                tasks = list(manager.tasks.values())
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def test_websocket_rechecks_revoked_session(self):
        socket = MagicMock()
        socket.headers, socket.cookies = {}, {'avito_session': 'fixture'}
        socket.url = 'ws://test/ws/parser/fixture'
        socket.accept, socket.close, socket.send_text = AsyncMock(), AsyncMock(), AsyncMock()
        manager = web_server.ParsingJobManager()
        manager.create_job('fixture', 'https://www.avito.ru/fixture', 1, 'http')
        with patch.object(web_server, 'job_manager', manager), patch.object(db, 'get_user_by_session', AsyncMock(side_effect=[object(), None])):
            await web_server.ws_parser(socket, 'fixture',job_manager=manager,db=db)
        socket.accept.assert_awaited_once()
        socket.close.assert_awaited_once_with(code=1008)
        self.assertEqual(manager.subscribers['fixture'], [])

    async def test_csrf_origin_and_viewer_permissions(self):
        self.client.headers.pop("X-CSRF-Token")
        self.assertEqual((await self.client.post("/api/settings", json=self.payload)).status_code, 403)
        self.client.headers["X-CSRF-Token"] = self.client.cookies["avito_csrf"]
        self.assertEqual((await self.client.post("/api/settings", json=self.payload, headers={"Origin": "https://evil.example"})).status_code, 403)
        _, _, user_id = await db.create_user("viewer", "fixture-viewer-2026", UserRole.VIEWER)
        token = await db.create_session(user_id)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web_server.app), base_url="http://test",
                                     headers={"Authorization": f"Bearer {token}"}) as viewer:
            self.assertEqual((await viewer.get("/api/dashboard")).status_code, 200)
            self.assertEqual((await viewer.get("/api/settings")).status_code, 403)
            self.assertEqual((await viewer.post("/api/monitoring/toggle")).status_code, 403)
            self.assertEqual((await viewer.post("/api/auth/logout")).status_code, 200)
            self.assertEqual((await viewer.get("/api/dashboard")).status_code, 401)

    async def test_password_change_and_admin_reset_revoke_sessions(self):
        user = await db.authenticate_user("admin", "fixture-password-2026")
        second = await db.create_session(user.id)
        response = await self.client.post("/api/auth/change-password", json={
            "old_password": "fixture-password-2026", "new_password": "new-fixture-password-2026"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(await db.get_user_by_session(second))
        self.assertEqual((await self.client.get("/api/dashboard")).status_code, 401)
        self.assertIsNotNone(await db.authenticate_user("admin", "new-fixture-password-2026"))
        token = await db.create_session(user.id)
        ok, _ = await db.update_user(user.id, new_password="another-fixture-password-2026")
        self.assertTrue(ok)
        self.assertIsNone(await db.get_user_by_session(token))
        expired = await db.create_session(user.id, days_valid=-1)
        self.assertIsNone(await db.get_user_by_session(expired))

    async def test_empty_database_has_no_known_admin_and_bootstrap_once(self):
        isolated = Database(self.root / "bootstrap.db")
        with patch.dict("os.environ", {}, clear=True):
            await isolated.init_db()
        self.assertEqual(await isolated.get_all_users(), [])
        results = await asyncio.gather(isolated.bootstrap_admin("admin", "first-fixture-password"),
                                       isolated.bootstrap_admin("other", "second-fixture-password"), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, ValueError) for result in results), 1)
        self.assertEqual(len(await isolated.get_all_users()), 1)
        self.assertIsNone(await isolated.authenticate_user("admin", "admin123"))

    async def test_login_throttled_before_hashing(self):
        web_server.login_limiter.entries.clear()
        for _ in range(5):
            response = await self.client.post("/api/auth/login", json={"username": "missing", "password": "bad"})
            self.assertEqual(response.status_code, 401)
        with patch.object(db, "authenticate_user") as authenticate:
            response = await self.client.post("/api/auth/login", json={"username": "missing", "password": "bad"})
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers["Retry-After"], "60")
            authenticate.assert_not_called()

    def test_limiter_expiry_capacity_and_origin(self):
        limiter = LoginLimiter(limit=1, capacity=2)
        with patch("security.time.monotonic", return_value=1):
            self.assertTrue(limiter.allow("one"))
            self.assertFalse(limiter.allow("one"))
        with patch("security.time.monotonic", return_value=62):
            self.assertTrue(limiter.allow("one"))
            limiter.allow("two")
            limiter.allow("three")
        self.assertEqual(len(limiter.entries), 2)
        self.assertTrue(same_origin("http://localhost:8000", "http://localhost:8000/path"))
        self.assertFalse(same_origin("null", "http://localhost:8000/path"))


if __name__ == "__main__":
    unittest.main()
