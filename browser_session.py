"""Atomic, generation-checked Avito cookies; unrelated browser profiles are untouched."""
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path


class CookieStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(self.path) + '.lock', 'a+b') as lock:
            lock.seek(0, 2)
            if not lock.tell():
                lock.write(b'0')
                lock.flush()
            lock.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                lock.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def snapshot(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(data, list):  # Existing installations.
                return {'generation': 'legacy', 'native_user_agent': False, 'cookies': data}
            if isinstance(data, dict) and isinstance(data.get('cookies'), list) and isinstance(data.get('generation'), str):
                return data
        except (OSError, ValueError):
            pass
        return {'generation': 'legacy', 'native_user_agent': False, 'cookies': []}

    def _write(self, data):
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name + '.', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(data, stream, ensure_ascii=False)
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def reset(self, expected_generation=None):
        with self.locked():
            state = self.snapshot()
            if expected_generation is not None and state['generation'] != expected_generation:
                return None
            state = {'generation': uuid.uuid4().hex, 'native_user_agent': True, 'cookies': []}
            self._write(state)
            return state['generation']

    def save(self, cookies, generation):
        with self.locked():
            state = self.snapshot()
            if state['generation'] != generation:
                return False  # An old running job must not restore invalidated cookies.
            state['cookies'] = cookies
            self._write(state)
            return True


class DetailParseError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
