import asyncio
import json
import random
import re
import time
from pathlib import Path
from typing import List, Optional
from playwright.async_api import BrowserContext, Page, async_playwright
from rich.console import Console

from config import config
from models import AvitoItem, ParseResult
from parser_core import AvitoDataExtractor
from proxy_manager import proxy_manager

console = Console()


class AvitoBrowserEngine:
    def __init__(self, headless: Optional[bool] = None, proxy: Optional[str] = None):
        self.headless = config.scraper.headless if headless is None else headless
        self.proxy_str = proxy or (proxy_manager.get_proxy() if config.proxy.enabled else None)
        self.cookies_file = config.scraper.cookies_file

    async def _create_context(self, p, override_proxy: Optional[str] = None) -> BrowserContext:
        """Создание контекста браузера с антидетект-настройками и защитой от фингерпринтинга"""
        active_proxy = override_proxy or self.proxy_str
        proxy_config = proxy_manager.get_playwright_proxy(active_proxy) if active_proxy else None

        browser = await p.chromium.launch(
            headless=self.headless,
            proxy=proxy_config,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--mute-audio",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1920,1080",
                "--disable-extensions",
            ]
        )

        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=config.scraper.user_agent,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            geolocation={"latitude": 55.7558, "longitude": 37.6173}, # Москва
            permissions=["geolocation"],
            color_scheme="dark"
        )

        # Инъекция глубокого антидетект скрипта
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU', 'ru', 'en-US', 'en'] });
            window.chrome = { runtime: {} };
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
            );
        """)

        # Загрузка сохраненных кук
        if config.scraper.save_cookies and self.cookies_file.exists():
            try:
                with open(self.cookies_file, "r", encoding="utf-8") as f:
                    cookies = json.load(f)
                    await context.add_cookies(cookies)
            except Exception:
                pass

        return context

    async def _save_cookies(self, context: BrowserContext) -> None:
        """Сохранение сессионных кук после успешного запроса"""
        if not config.scraper.save_cookies:
            return
        try:
            self.cookies_file.parent.mkdir(parents=True, exist_ok=True)
            cookies = await context.cookies()
            with open(self.cookies_file, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    async def _emulate_human(self, page: Page) -> None:
        """Имитация плавного человеческого скролла, чтения и микродвижений мыши"""
        try:
            # Случайные микро-движения мыши
            for _ in range(random.randint(2, 4)):
                x = random.randint(200, 1200)
                y = random.randint(200, 800)
                await page.mouse.move(x, y, steps=random.randint(5, 12))
                await asyncio.sleep(random.uniform(0.1, 0.3))

            # Скролл вниз порциями
            for _ in range(random.randint(2, 4)):
                scroll_y = random.randint(300, 700)
                await page.mouse.wheel(0, scroll_y)
                await asyncio.sleep(random.uniform(0.4, 0.9))

            # Легкий откат наверх
            await page.mouse.wheel(0, -random.randint(100, 250))
            await asyncio.sleep(random.uniform(0.2, 0.5))

        except Exception:
            pass

    async def _check_and_handle_block(self, page: Page) -> bool:
        """
        Проверка на реальную блокировку IP (429) или вызов капчи.
        Если запущен в видимом режиме (headless=False) — дает пользователю время решить капчу.
        """
        try:
            title = await page.title()
            content = await page.content()
            url = page.url
        except Exception:
            return False

        is_blocked = (
            "Доступ ограничен" in title or
            ("Доступ ограничен" in content and "проблема с IP" in content) or
            "firewall" in title.lower() or
            "smartcaptcha" in content.lower() or
            "validate_captcha" in url or
            "blocked" in url
        )

        if is_blocked:
            if not self.headless:
                console.print("\n[bold yellow]⚠️ Внимание![/bold yellow] Обнаружена проверка/капча от Авито.")
                console.print("[cyan]Пожалуйста, решите капчу в открытом окне браузера.[/cyan]")
                console.print("[dim]Ожидание прохождения проверки (до 60 секунд)...[/dim]")
                for _ in range(30):
                    await asyncio.sleep(2)
                    try:
                        cur_title = await page.title()
                        cur_content = await page.content()
                        if "Доступ ограничен" not in cur_title and "проблема с IP" not in cur_content:
                            console.print("[bold green]✓ Проверка успешно пройдена![/bold green]")
                            return False
                    except Exception:
                        break
            return True

        return False

    def _build_page_url(self, base_url: str, page_num: int) -> str:
        """Корректное добавление параметра страницы в URL Авито"""
        if page_num <= 1:
            return base_url

        if "p=" in base_url:
            return re.sub(r'([?&])p=\d+', rf'\g<1>p={page_num}', base_url)
        
        separator = "&" if "?" in base_url else "?"
        return f"{base_url}{separator}p={page_num}"

    async def parse_search(
        self,
        url: str,
        max_pages: int = 1,
        progress_callback: Optional[callable] = None
    ) -> ParseResult:
        """
        Парсинг поисковой выдачи Авито на заданное количество страниц с защитой от сбоев.
        """
        start_time = time.time()
        result = ParseResult()
        all_items: List[AvitoItem] = []
        seen_ids = set()

        async with async_playwright() as p:
            context = await self._create_context(p)
            page = await context.new_page()

            for page_num in range(1, max_pages + 1):
                page_url = self._build_page_url(url, page_num)
                console.print(f"[dim]Загрузка страницы {page_num}/{max_pages}:[/dim] {page_url}")

                page_loaded = False
                for attempt in range(1, 3):
                    try:
                        await page.goto(page_url, wait_until="domcontentloaded", timeout=config.scraper.timeout_ms)
                        await asyncio.sleep(random.uniform(1.5, 2.5))

                        if await self._check_and_handle_block(page):
                            # Если прокси включены, пробуем ротировать прокси
                            if config.proxy.enabled and config.proxy.rotate:
                                new_proxy = proxy_manager.get_proxy()
                                if new_proxy and new_proxy != self.proxy_str:
                                    console.print(f"[yellow]Ротация прокси -> {new_proxy}[/yellow]")
                                    await context.close()
                                    context = await self._create_context(p, override_proxy=new_proxy)
                                    page = await context.new_page()
                                    continue

                            err_msg = f"Страница {page_num} заблокирована Авито (IP ограничение / Капча)."
                            result.errors.append(err_msg)
                            break

                        await self._emulate_human(page)
                        html = await page.content()

                        # Извлечение объявлений
                        page_items = AvitoDataExtractor.extract_from_initial_data(html)
                        if not page_items:
                            page_items = AvitoDataExtractor.extract_from_dom(html)

                        new_on_page = 0
                        for item in page_items:
                            if item.id not in seen_ids:
                                seen_ids.add(item.id)
                                all_items.append(item)
                                new_on_page += 1

                        console.print(f"[green]✓ Страница {page_num}:[/green] найдено {len(page_items)} объявлений (новых {new_on_page})")

                        if progress_callback:
                            await progress_callback(page_num, max_pages, len(all_items))

                        page_loaded = True
                        break

                    except Exception as e:
                        console.print(f"[yellow]Попытка {attempt} для стр. {page_num} завершилась ошибкой: {e}[/yellow]")
                        if attempt == 2:
                            result.errors.append(f"Ошибка загрузки стр. {page_num}: {e}")
                        await asyncio.sleep(2)

                if not page_loaded or len(all_items) == 0:
                    break

                # Пауза между страницами
                delay = random.uniform(config.scraper.page_delay_min, config.scraper.page_delay_max)
                await asyncio.sleep(delay)

            await self._save_cookies(context)
            await context.browser.close()

        result.items = all_items
        result.total_found = len(all_items)
        result.elapsed_seconds = round(time.time() - start_time, 2)
        return result

    async def parse_item_detail(self, item_url: str) -> Optional[AvitoItem]:
        """
        Детальный сбор карточки конкретного объявления со всеми фото и характеристиками.
        """
        async with async_playwright() as p:
            context = await self._create_context(p)
            page = await context.new_page()

            try:
                await page.goto(item_url, wait_until="domcontentloaded", timeout=config.scraper.timeout_ms)
                await asyncio.sleep(random.uniform(1.5, 3.0))

                if await self._check_and_handle_block(page):
                    return None

                await self._emulate_human(page)
                html = await page.content()
                await self._save_cookies(context)

                match = re.search(r'_(\d+)(?:\?|$)', item_url)
                item_id = match.group(1) if match else "unknown"

                item = AvitoDataExtractor.parse_item_detail_page(html, item_id, item_url)
                await context.browser.close()
                return item

            except Exception as e:
                console.print(f"[red]Ошибка парсинга карточки {item_url}: {e}[/red]")
                await context.browser.close()
                return None


browser_engine = AvitoBrowserEngine()
