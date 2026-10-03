"""Redact credentials before log messages reach the UI or configured handlers."""

import logging
import re
import json
import time
import secrets
import functools
from contextvars import ContextVar
from datetime import datetime, timezone
from config import config

log_context = ContextVar("log_context", default={})


class JsonFormatter(logging.Formatter):
    def format(self, record):
        value = {"timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                 "level": record.levelname, "logger": record.name,
                 "message": redact(record.getMessage()), **log_context.get()}
        if hasattr(record,'duration_seconds'):
            value['duration_seconds'] = record.duration_seconds
        if record.exc_info:
            value['exception'] = redact(self.formatException(record.exc_info))
        return json.dumps(value, ensure_ascii=False)


def configure_logging():
    root = logging.getLogger()
    if not any(getattr(handler, 'avito_json', False) for handler in root.handlers):
        handler = logging.StreamHandler()
        handler.avito_json = True
        handler.setFormatter(JsonFormatter())
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)
    root.setLevel(logging.INFO)


def observed(operation):
    def decorate(function):
        @functools.wraps(function)
        async def wrapped(*args, **kwargs):
            parent = log_context.get()
            context = {**parent, 'run_id': secrets.token_hex(8), 'operation': operation}
            if parent.get('run_id'):
                context['parent_run_id'] = parent['run_id']
            if operation == 'search' and len(args) > 1:
                context['search_id'] = args[1].id
            token = log_context.set(context)
            started = time.perf_counter()
            try:
                return await function(*args, **kwargs)
            finally:
                elapsed = time.perf_counter()-started
                if operation == 'monitoring-cycle' and args:
                    args[0].last_duration_seconds = elapsed
                logging.getLogger('AvitoOperations').info('operation finished in %.3fs', elapsed, extra={'duration_seconds':elapsed})
                log_context.reset(token)
        return wrapped
    return decorate


def redact(value):
    text = str(value)
    if config.telegram.bot_token:
        text = text.replace(config.telegram.bot_token, "[REDACTED]")
    text = re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[REDACTED]@", text)
    text = re.sub(r"(api\.telegram\.org/bot)[^/\s]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(bearer\s+)[^\s,\"\']+", r"\1[REDACTED]", text)
    text = re.sub(
        r"(?i)((?:api_key|password|secret|token)[\"\']?\s*[:=]\s*[\"\']?)[^\s,\"\']+",
        r"\1[REDACTED]",
        text,
    )
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record):
        record.msg, record.args = redact(record.getMessage()), ()
        return True
