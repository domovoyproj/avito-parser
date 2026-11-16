"""Avito listing references shared by Telegram and CRM duplicate detection."""
import re
from urllib.parse import urlsplit, urlunsplit, unquote
from security import avito_url


def listing_reference(value):
    if not isinstance(value, str) or len(value) > 2000 or re.search(r'\s', value) or not avito_url(value):
        return None
    parts = urlsplit(value)
    path = unquote(parts.path).rstrip('/')
    match = re.search(r'(?:_|/)(\d{5,})$', path)
    if not match:
        return None
    return match[1], urlunsplit(('https', 'www.avito.ru', parts.path.rstrip('/'), '', ''))


def message_links(message):
    text = message.text or message.caption or ''
    candidates = [e.url for e in (message.entities or message.caption_entities or []) if e.type == 'text_link' and e.url]
    candidates.extend(re.findall(r'https?://[^\s<>"\u00ab\u00bb]+', text))
    result = {}
    for candidate in candidates:
        ref = listing_reference(candidate.rstrip('.,!?;)>]'))
        if ref:
            result[ref[0]] = ref[1]
    return list(result.values())


def import_button(item_id):
    value = 'crm:import:' + str(item_id)
    if len(value.encode('utf-8')) > 64:
        return {'text': 'В CRM', 'callback_data': 'crm:prompt:import'}
    return {'text': 'В CRM', 'callback_data': value}
