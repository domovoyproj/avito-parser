"""Shared extraction outcomes. A blocked page is never treated as empty."""
from models import ParseResult
import re
from parser_core import AvitoDataExtractor


def extract_page(html):
    lowered = html.lower()
    if any(marker in lowered for marker in ('data-marker="captcha', 'доступ ограничен', 'проверка браузера', '<title>captcha')):
        return ParseResult(outcome="blocked", errors=["Источник ограничил доступ; сбор остановлен"])
    items = AvitoDataExtractor.extract_from_initial_data(html)
    source = "json"
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
