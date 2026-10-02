import random
from pathlib import Path
from typing import Dict, List, Optional
import httpx
from config import config


class ProxyManager:
    def __init__(self, proxies_file: Optional[Path] = None, default_proxy: Optional[str] = None):
        self.proxies_file = proxies_file or config.proxy.proxies_file
        # None means use configured default; an explicit empty string clears it.
        self.default_proxy = config.proxy.default_proxy if default_proxy is None else default_proxy
        self.proxies: List[str] = []
        self._current_index = 0
        self.load_proxies()

    def load_proxies(self) -> None:
        """Загрузка списка прокси из файла и конфига"""
        self.proxies = []
        
        if self.default_proxy:
            norm = self._normalize_proxy(self.default_proxy)
            if norm:
                self.proxies.append(norm)

        if self.proxies_file and self.proxies_file.exists():
            with open(self.proxies_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        norm = self._normalize_proxy(line)
                        if norm and norm not in self.proxies:
                            self.proxies.append(norm)

    def _normalize_proxy(self, proxy_str: str) -> Optional[str]:
        """
        Нормализация прокси в стандартный URL формат.
        Поддерживает:
        - http://user:pass@host:port
        - socks5://user:pass@host:port
        - host:port:user:pass
        - host:port
        """
        proxy_str = proxy_str.strip()
        if not proxy_str:
            return None

        if proxy_str.startswith("http://") or proxy_str.startswith("https://") or proxy_str.startswith("socks5://"):
            return proxy_str

        parts = proxy_str.split(":")
        if len(parts) == 4:
            # host:port:user:pass
            host, port, user, pwd = parts
            return f"http://{user}:{pwd}@{host}:{port}"
        elif len(parts) == 2:
            # host:port
            host, port = parts
            return f"http://{host}:{port}"
        
        return proxy_str

    def get_proxy(self) -> Optional[str]:
        """Получить следующий прокси по ротации или None"""
        if not self.proxies:
            return None

        if config.proxy.rotate:
            proxy = self.proxies[self._current_index % len(self.proxies)]
            self._current_index += 1
            return proxy
        else:
            return self.proxies[0]

    def get_random_proxy(self) -> Optional[str]:
        """Получить случайный прокси из пула"""
        if not self.proxies:
            return None
        return random.choice(self.proxies)

    def get_playwright_proxy(self, proxy_url: Optional[str] = None) -> Optional[Dict[str, str]]:
        """Преобразование прокси в формат для Playwright"""
        proxy = proxy_url or self.get_proxy()
        if not proxy:
            return None

        # Playwright принимает {'server': '...', 'username': '...', 'password': '...'}
        # или просто {'server': 'http://user:pass@host:port'}
        return {"server": proxy}

    async def check_proxy(self, proxy_url: str, timeout_sec: int = 7) -> bool:
        """Проверка работоспособности прокси"""
        try:
            async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout_sec) as client:
                resp = await client.get("https://httpbin.org/ip")
                return resp.status_code == 200
        except Exception:
            return False

    def add_proxy(self, proxy_str: str) -> bool:
        """Добавить прокси в список и сохранить в файл"""
        norm = self._normalize_proxy(proxy_str)
        if norm and norm not in self.proxies:
            self.proxies.append(norm)
            self.proxies_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.proxies_file, "a", encoding="utf-8") as f:
                f.write(f"{norm}\n")
            return True
        return False

    @property
    def total_count(self) -> int:
        return len(self.proxies)


proxy_manager = ProxyManager()
