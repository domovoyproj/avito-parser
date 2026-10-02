"""Offline settings regressions; never read/write the user's .env or database."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values
from pydantic import ValidationError

import config as configuration
from config import AppConfig, ScraperConfig
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


if __name__ == "__main__":
    unittest.main()
