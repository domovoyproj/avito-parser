import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
import test_config
from dependencies import get_database
from web_server import app


class DependencyTests(unittest.IsolatedAsyncioTestCase):
    setUp = test_config.SettingsAPITests.setUp
    asyncSetUp = test_config.SettingsAPITests.asyncSetUp

    async def test_repository_override_routes_without_global_database_mutation(self):
        replacement = SimpleNamespace(get_stats=AsyncMock(return_value={'isolated': True}))
        app.dependency_overrides[get_database] = lambda: replacement
        try:
            response = await self.client.get('/api/stats')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {'isolated': True})
            replacement.get_stats.assert_awaited_once()
        finally:
            app.dependency_overrides.pop(get_database, None)
