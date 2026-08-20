import asyncio
import json
import random
import time
from typing import Dict, List, Optional
from curl_cffi.requests import AsyncSession
from rich.console import Console

from config import config
from models import AvitoItem, ParseResult
from parser_core import AvitoDataExtractor
from proxy_manager import proxy_manager

console = Console()


class AvitoHttpEngine:
    def __init__(self, proxy: Optional[str] = None):
        self.proxy = proxy or (proxy_manager.get_proxy() if config.proxy.enabled else None)
        self.cookies_file = config.scraper.cookies_file

    def _get_headers(self) -> dict:
        return {
            "User-Agent": config.scraper.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Sec-Ch-Ua": '"Google Chrome";v="133", "Chromium";v="133", "Not_A Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }

    def _load_cookies_dict(self) -> Dict[str, str]:
        """Загрузка кук из cookies.json для передачи в HTTP сессию"""
        cookies_dict = {}
        if config.scraper.save_cookies and self.cookies_file.exists():
            try:
                with open(self.cookies_file, "r", encoding="utf-8") as f:
                    cookies_list = json.load(f)
                    for c in cookies_list:
                        if isinstance(c, dict) and "name" in c and "value" in c:
                            cookies_dict[c["name"]] = c["value"]
            except Exception:
                pass
        return cookies_dict

    async def parse_search(self, url: str, max_pages: int = 1) -> ParseResult:
        """
        Быстрый HTTP парсинг поисковой выдачи через curl_cffi с TLS-мимикрией Chrome и сессионными куками.
        """
        start_time = time.time()
        result = ParseResult()
        all_items: List[AvitoItem] = []
        seen_ids = set()
        cookies = self._load_cookies_dict()

        async with AsyncSession(impersonate="chrome131", proxy=self.proxy, cookies=cookies) as session:
            for page_num in range(1, max_pages + 1):
                page_url = url if page_num == 1 else f"{url}&p={page_num}" if "?" in url else f"{url}?p={page_num}"
                console.print(f"[dim]HTTP запрос к странице {page_num}:[/dim] {page_url}")

                page_success = False
                for attempt in range(1, 3):
                    try:
                        resp = await session.get(page_url, headers=self._get_headers(), timeout=20)
                        
                        if resp.status_code != 200:
                            if resp.status_code == 429 or resp.status_code == 403:
                                err_msg = f"HTTP {resp.status_code}: Доступ ограничен. Рекомендуется включить прокси или движок Playwright."
                            else:
                                err_msg = f"HTTP статус {resp.status_code}"
                            result.errors.append(err_msg)
                            console.print(f"[yellow]⚠️ {err_msg}[/yellow]")
                            break

                        html = resp.text
                        page_items = AvitoDataExtractor.extract_from_initial_data(html)
                        if not page_items:
                            page_items = AvitoDataExtractor.extract_from_dom(html)

                        new_on_page = 0
                        for item in page_items:
                            if item.id not in seen_ids:
                                seen_ids.add(item.id)
                                all_items.append(item)
                                new_on_page += 1

                        console.print(f"[green]✓ Страница {page_num}:[/green] получено {len(page_items)} объявлений")
                        page_success = True
                        break

                    except Exception as e:
                        if attempt == 2:
                            err_msg = f"Ошибка HTTP запроса: {e}"
                            result.errors.append(err_msg)
                            console.print(f"[red]✗ {err_msg}[/red]")
                        await asyncio.sleep(1.5)

                if not page_success or len(all_items) == 0:
                    break

                await asyncio.sleep(random.uniform(1.0, 2.5))

        result.items = all_items
        result.total_found = len(all_items)
        result.elapsed_seconds = round(time.time() - start_time, 2)
        return result


http_engine = AvitoHttpEngine()
