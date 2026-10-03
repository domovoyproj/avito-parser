"""Offline settings regressions; never read/write the user's .env or database."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from dotenv import dotenv_values
from pydantic import ValidationError

import config as configuration
from config import AppConfig, ScraperConfig, ProxyConfig, TelegramConfig
from database import db
from models import AvitoItem
import http_engine as http_module
import web_server
from web_server import SaveSettingsRequest


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env_file = self.root / ".env"
        self.env_patch = patch.object(configuration, "ENV_FILE", self.env_file)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def load(self):
        # Redirect directory creation as well as the environment file.
        class IsolatedConfig(AppConfig):
            data_dir: Path = self.root / "data"
            export_dir: Path = self.root / "exports"
        return IsolatedConfig.load()

    def test_round_trip_and_unknown_keys(self):
        self.env_file.write_text("# keep comment\nAI_API_KEY='fixture-only'\nCUSTOM=value\n", encoding="utf-8")
        settings = AppConfig()
        settings.scraper.timeout_ms = 12000
        settings.scraper.page_delay_min = 0.5
        settings.scraper.page_delay_max = 1.5
        settings.telegram.send_photos = False
        settings.proxy.enabled = True
        settings.proxy.default_proxy = "http://user:p#ss'word@host:8080"
        settings.save_to_env()
        loaded = self.load()
        self.assertEqual(loaded.scraper.timeout_ms, 12000)
        self.assertEqual((loaded.scraper.page_delay_min, loaded.scraper.page_delay_max), (0.5, 1.5))
        self.assertFalse(loaded.telegram.send_photos)
        self.assertEqual(loaded.proxy.default_proxy, settings.proxy.default_proxy)
        values = dotenv_values(self.env_file)
        self.assertEqual(values["AI_API_KEY"], "fixture-only")
        self.assertEqual(values["CUSTOM"], "value")
        self.assertIn("# keep comment", self.env_file.read_text(encoding="utf-8"))

    def test_environment_overrides_file(self):
        self.env_file.write_text("SCRAPER_TIMEOUT_MS=12000\n", encoding="utf-8")
        os.environ["SCRAPER_TIMEOUT_MS"] = "9000"
        self.assertEqual(self.load().scraper.timeout_ms, 9000)

    def test_invalid_environment_uses_defaults(self):
        for value in ("bad", "-1", "nan", "inf", "0"):
            with self.subTest(value=value), patch.dict(os.environ, {
                "SCRAPER_TIMEOUT_MS": value, "SCRAPER_PAGE_DELAY_MIN": "nan",
                "SCRAPER_PAGE_DELAY_MAX": "inf",
            }, clear=True):
                loaded = self.load().scraper
                self.assertEqual(loaded.timeout_ms, 45000)
                self.assertEqual((loaded.page_delay_min, loaded.page_delay_max), (2.0, 5.0))

    def test_reversed_delays_fall_back(self):
        os.environ.update(SCRAPER_PAGE_DELAY_MIN="10", SCRAPER_PAGE_DELAY_MAX="1")
        loaded = self.load().scraper
        self.assertEqual((loaded.page_delay_min, loaded.page_delay_max), (2.0, 5.0))

    def test_clear_proxy_removes_old_value(self):
        self.env_file.write_text("DEFAULT_PROXY='http://old:8080'\n", encoding="utf-8")
        settings = AppConfig()
        settings.save_to_env()
        self.assertIsNone(self.load().proxy.default_proxy)

    def test_failed_replace_preserves_file(self):
        original = "CUSTOM=retained\n"
        self.env_file.write_text(original, encoding="utf-8")
        with patch.object(configuration.os, "replace", side_effect=OSError("fixture failure")):
            with self.assertRaises(OSError):
                AppConfig().save_to_env()
        self.assertEqual(self.env_file.read_text(encoding="utf-8"), original)
        self.assertEqual(list(self.root.glob(".env-*.tmp")), [])

    def test_api_rejects_invalid_scraper_settings(self):
        payload = dict(headless=True, timeout_ms=1000, page_delay_min=0,
                       page_delay_max=1, proxy_enabled=False)
        for changes in ({"timeout_ms": 0}, {"page_delay_min": -1},
                        {"page_delay_max": float("inf")}, {"page_delay_min": float("nan")},
                        {"page_delay_min": 2, "page_delay_max": 1}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                SaveSettingsRequest(**(payload | changes))
        self.assertEqual(SaveSettingsRequest(**payload).page_delay_min, 0)

    def test_invalid_assignment_does_not_write(self):
        settings = AppConfig()
        settings.scraper.page_delay_min = 10
        with self.assertRaises(ValidationError):
            settings.save_to_env()
        self.assertFalse(self.env_file.exists())


class SettingsAPITests(unittest.IsolatedAsyncioTestCase):
    setUp = ConfigTests.setUp
    load = ConfigTests.load

    async def asyncSetUp(self):
        self.settings = configuration.config
        for name, value in (
            ("scraper", ScraperConfig()),
            ("proxy", ProxyConfig(proxies_file=self.root / "proxies.txt")),
            ("telegram", TelegramConfig()),
        ):
            context = patch.object(self.settings, name, value)
            context.start()
            self.addCleanup(context.stop)
        context = patch.object(db, "db_path", self.root / "test.db")
        context.start()
        self.addCleanup(context.stop)
        for obj, name in ((web_server.browser_engine, "headless"),
                          (web_server.browser_engine, "proxy_str"),
                          (web_server.http_engine, "proxy"),
                          (web_server.proxy_manager, "default_proxy"),
                          (web_server.proxy_manager, "proxies"),
                          (web_server.proxy_manager, "_current_index")):
            context = patch.object(obj, name, getattr(obj, name))
            context.start()
            self.addCleanup(context.stop)
        await db.init_db()
        await db.bootstrap_admin("admin", "fixture-password-2026")
        web_server.login_limiter.entries.clear()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=web_server.app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        self.payload = dict(headless=False, timeout_ms=7500, page_delay_min=0.1,
                            page_delay_max=0.2, proxy_enabled=True,
                            default_proxy="127.0.0.1:8080", send_photos=False,
                            notification_interval_min=15, admin_chat_ids=[123])
        response = await self.client.post("/api/auth/login", json={"username": "admin", "password": "fixture-password-2026"})
        self.assertEqual(response.status_code, 200)
        self.client.headers["X-CSRF-Token"] = self.client.cookies["avito_csrf"]

    async def test_api_round_trip_updates_running_engines(self):
        self.env_file.write_text("CUSTOM=retained\n", encoding="utf-8")
        response = await self.client.post("/api/settings", json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        saved = (await self.client.get("/api/settings")).json()
        self.assertEqual(saved["scraper"]["timeout_ms"], 7500)
        self.assertFalse(web_server.browser_engine.headless)
        self.assertEqual(web_server.browser_engine.proxy_str, "http://127.0.0.1:8080")
        self.assertEqual(web_server.http_engine.proxy, "http://127.0.0.1:8080")
        restarted = self.load()
        self.assertEqual(restarted.scraper.timeout_ms, 7500)
        self.assertFalse(restarted.telegram.send_photos)
        self.assertEqual(restarted.telegram.notification_interval_min, 15)
        self.assertEqual(dotenv_values(self.env_file)["CUSTOM"], "retained")
        response = await self.client.post("/api/settings", json=self.payload | {"proxy_enabled": False, "default_proxy": None})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(web_server.browser_engine.proxy_str)
        self.assertIsNone(web_server.http_engine.proxy)
        self.assertIsNone(web_server.proxy_manager.default_proxy)
        self.assertEqual(web_server.proxy_manager.proxies, [])

    async def test_failed_save_keeps_runtime_and_file(self):
        original = "CUSTOM=retained\n"
        self.env_file.write_text(original, encoding="utf-8")
        before = self.settings.model_dump()
        engine_before = (web_server.browser_engine.headless, web_server.browser_engine.proxy_str,
                         web_server.http_engine.proxy, list(web_server.proxy_manager.proxies))
        with patch.object(configuration.os, "replace", side_effect=OSError("sensitive fixture path")):
            response = await self.client.post("/api/settings", json=self.payload)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("sensitive", response.text)
        self.assertEqual(self.settings.model_dump(), before)
        self.assertEqual(engine_before, (web_server.browser_engine.headless, web_server.browser_engine.proxy_str,
                                        web_server.http_engine.proxy, web_server.proxy_manager.proxies))
        self.assertEqual(self.env_file.read_text(encoding="utf-8"), original)

    async def test_api_invalid_and_anonymous_cannot_write(self):
        for changes in ({"timeout_ms": 0}, {"notification_interval_min": 0},
                        {"page_delay_min": 10}, {"page_delay_max": -1}):
            response = await self.client.post("/api/settings", json=self.payload | changes)
            self.assertEqual(response.status_code, 422)
        self.assertFalse(self.env_file.exists())
        await self.client.post("/api/auth/logout")
        response = await self.client.post("/api/settings", json=self.payload)
        self.assertEqual(response.status_code, 401)
        self.assertFalse(self.env_file.exists())

    async def test_http_uses_saved_timeout_delay_and_proxy(self):
        await self.client.post("/api/settings", json=self.payload)
        session = MagicMock()
        session.get = AsyncMock(return_value=MagicMock(status_code=200, text="fixture"))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch.object(http_module, "AsyncSession", return_value=session) as factory, \
             patch.object(http_module.AvitoDataExtractor, "extract_from_initial_data", return_value=[
                 AvitoItem(id="fixture", title="fixture", url="https://www.avito.ru/fixture")]), \
             patch.object(http_module.asyncio, "sleep", new_callable=AsyncMock) as sleep, \
             patch.object(http_module.random, "uniform", return_value=0.15) as uniform:
            result = await web_server.http_engine.parse_search("https://www.avito.ru/fixture", max_pages=2)
        self.assertEqual(result.total_found, 1)
        self.assertEqual(factory.call_args.kwargs["proxy"], "http://127.0.0.1:8080")
        self.assertEqual(session.get.await_count, 2)
        for call in session.get.await_args_list:
            self.assertEqual(call.kwargs["timeout"], 7.5)
        uniform.assert_called_once_with(0.1, 0.2)
        sleep.assert_awaited_once_with(0.15)


if __name__ == "__main__":
    unittest.main()
