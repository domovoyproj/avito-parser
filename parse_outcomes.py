"""Shared extraction outcomes. A blocked page is never treated as empty."""
from models import ParseResult
import re
from parser_core import AvitoDataExtractor
from access_guard import access_challenge


def extract_page(html):
    lowered = html.lower()
    if access_challenge(html):
        return ParseResult(outcome="blocked", errors=["Источник ограничил доступ; сбор остановлен"])
    items = AvitoDataExtractor.extract_from_initial_data(html)
    source = "json"
    if items and any(not item.images for item in items):
        dom = {item.id: item for item in AvitoDataExtractor.extract_from_dom(html)}
        for item in items:
            fallback = dom.get(item.id)
            if not item.images and fallback and fallback.images:
                item.images, item.main_image = fallback.images, fallback.main_image
    if not items:
        items = AvitoDataExtractor.extract_from_dom(html)
        source = "html"
    if not items and not (any(marker in lowered for marker in ('ничего не найдено','объявлений не найдено','no results')) or re.search(r'"items"\s*:\s*\[\s*\]',html)):
        return ParseResult(outcome='error',source=source,errors=['Не удалось распознать выдачу источника'])
    return ParseResult(items=items, total_found=len(items), outcome="success" if items else "empty", source=source)


def finalize(result):
    if result.outcome == "blocked":
        return result
    result.outcome = ("partial" if result.items else "error") if result.errors else ("success" if result.items else "empty")
    return result


def extract_detail(html, url):
    """Require actual item evidence, not a generic error page with an h1."""
    if access_challenge(html):
        return None
    match = re.search(r'_(\d+)(?:\?|$)', url)
    if not match:
        return None
    item_id = match.group(1)
    structured = AvitoDataExtractor.extract_from_initial_data(html)
    matching = next((item for item in structured if item.id == item_id), None)
    if not matching and not re.search(r'(?:item-view/title-info|item-view/item-price|item-description/(?:text|html)|itemprop=[\"\x27]price)', html):
        return None
    item = AvitoDataExtractor.parse_item_detail_page(html, item_id, url)
    return item if item and item.title != 'Без названия' else matching
