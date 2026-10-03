import asyncio
import unittest
from unittest.mock import patch
import httpx
from models import UserRole
from database import Database, db
from security import LoginLimiter, same_origin
import test_config
import web_server


class SecurityTests(unittest.IsolatedAsyncioTestCase):
    setUp = test_config.SettingsAPITests.setUp
    asyncSetUp = test_config.SettingsAPITests.asyncSetUp

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
