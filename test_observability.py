import unittest
from observability import redact
from security import avito_url


class OperationalTests(unittest.TestCase):
    def test_log_secrets_and_proxy_password_redacted(self):
        value = redact('Authorization: Bearer credential api_key=privatekey password=privatepass https://user:proxypass@proxy.test api.telegram.org/bot123:abc/sendMessage')
        for secret in ('credential', 'privatekey', 'privatepass', 'proxypass', '123:abc', 'user:'):
            self.assertNotIn(secret, value)
        self.assertIn('[REDACTED]', value)

    def test_avito_url_rejects_substring_spoofing(self):
        self.assertTrue(avito_url('https://www.avito.ru/moskva'))
        self.assertFalse(avito_url('https://avito.ru.attacker.test/'))
        self.assertFalse(avito_url('https://attacker.test/avito.ru'))
        self.assertFalse(avito_url('http://user:password@avito.ru/'))
        self.assertFalse(avito_url('file:///avito.ru'))
