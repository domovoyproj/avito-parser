"""Redact credentials before log messages reach the UI or configured handlers."""
import logging
import re
from config import config


def redact(value):
    text = str(value)
    if config.telegram.bot_token:
        text = text.replace(config.telegram.bot_token, '[REDACTED]')
    text = re.sub(r'(https?://)[^/@\s]+:[^/@\s]+@', r'\1[REDACTED]@', text)
    text = re.sub(r'(api\.telegram\.org/bot)[^/\s]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)(bearer\s+)[^\s,\"\']+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)((?:api_key|password|secret|token)[\"\']?\s*[:=]\s*[\"\']?)[^\s,\"\']+', r'\1[REDACTED]', text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record):
        record.msg, record.args = redact(record.getMessage()), ()
        return True
