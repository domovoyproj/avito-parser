"""Bounded authentication throttling and browser-origin checks."""
import time
from collections import OrderedDict, deque
from urllib.parse import urlsplit


class LoginLimiter:
    def __init__(self, limit=5, window=60, capacity=4096):
        self.limit, self.window, self.capacity = limit, window, capacity
        self.entries = OrderedDict()

    def allow(self, address):
        now = time.monotonic()
        attempts = self.entries.pop(address, deque())
        while attempts and attempts[0] <= now - self.window:
            attempts.popleft()
        allowed = len(attempts) < self.limit
        if allowed:
            attempts.append(now)
        self.entries[address] = attempts
        while len(self.entries) > self.capacity:
            self.entries.popitem(last=False)
        return allowed


def same_origin(origin, url):
    try:
        left, right = urlsplit(origin), urlsplit(str(url))
        return (left.scheme, left.hostname, left.port or (443 if left.scheme == "https" else 80)) == (
            right.scheme, right.hostname, right.port or (443 if right.scheme == "https" else 80))
    except ValueError:
        return False


login_limiter = LoginLimiter()


def avito_url(value):
    try:
        url = urlsplit(value)
        host = (url.hostname or '').lower()
        return url.scheme in ('https', 'http') and (host == 'avito.ru' or host.endswith('.avito.ru')) and not url.username and not url.password and url.port in (None, 80, 443)
    except (ValueError, TypeError):
        return False
