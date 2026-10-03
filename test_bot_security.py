import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from config import config
from telegram_bot import AdminCommandMiddleware


class BotSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_commands_require_configured_admin_identity(self):
        handler = AsyncMock(return_value='allowed')
        with patch.object(config.telegram, 'admin_chat_ids', [123]):
            middleware = AdminCommandMiddleware()
            await middleware(handler, SimpleNamespace(from_user=SimpleNamespace(id=456)), {})
            handler.assert_not_awaited()
            result = await middleware(handler, SimpleNamespace(from_user=SimpleNamespace(id=123)), {})
            self.assertEqual(result, 'allowed')
            handler.assert_awaited_once()
