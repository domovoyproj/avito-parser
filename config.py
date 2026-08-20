import os
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE)


class ScraperConfig(BaseModel):
    headless: bool = Field(default=True, description="Запуск браузера в скрытом режиме (Headless)")
    timeout_ms: int = Field(default=45000, description="Таймаут загрузки страницы в мс")
    page_delay_min: float = Field(default=2.0, description="Минимальная пауза между страницами в сек")
    page_delay_max: float = Field(default=5.0, description="Максимальная пауза между страницами в сек")
    max_pages: int = Field(default=3, description="Максимальное количество страниц выдачи по умолчанию")
    user_agent: str = Field(
        default="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        description="User-Agent браузера"
    )
    save_cookies: bool = Field(default=True, description="Сохранять сессионные куки между запусками")
    cookies_file: Path = Field(default=BASE_DIR / "data" / "cookies.json")


class ProxyConfig(BaseModel):
    enabled: bool = Field(default=False, description="Использовать прокси")
    proxies_file: Path = Field(default=BASE_DIR / "proxies.txt", description="Файл со списком прокси")
    default_proxy: Optional[str] = Field(default=None, description="Единый прокси (например socks5://user:pass@host:port)")
    rotate: bool = Field(default=True, description="Ротировать прокси при каждом запросе/сессии")


class TelegramConfig(BaseModel):
    bot_token: str = Field(default="", description="Токен Telegram бота от @BotFather")
    admin_chat_ids: List[int] = Field(default_factory=list, description="Список ID пользователей с правами админа")
    notification_interval_min: int = Field(default=10, description="Интервал проверки мониторинга в минутах")
    send_photos: bool = Field(default=True, description="Прикреплять фото к уведомлениям")


class WebConfig(BaseModel):
    host: str = Field(default="127.0.0.1", description="Хост для запуска веб-панели")
    port: int = Field(default=8000, description="Порт веб-панели")
    auto_open_browser: bool = Field(default=True, description="Автоматически открывать браузер при запуске")


class AppConfig(BaseModel):
    base_dir: Path = BASE_DIR
    data_dir: Path = BASE_DIR / "data"
    export_dir: Path = BASE_DIR / "exports"
    db_path: Path = BASE_DIR / "data" / "avito.db"
    
    scraper: ScraperConfig = ScraperConfig()
    proxy: ProxyConfig = ProxyConfig()
    telegram: TelegramConfig = TelegramConfig()
    web: WebConfig = WebConfig()

    @classmethod
    def load(cls) -> "AppConfig":
        config = cls()
        config.data_dir.mkdir(parents=True, exist_ok=True)
        config.export_dir.mkdir(parents=True, exist_ok=True)

        # Чтение из переменных окружения
        headless_env = os.getenv("SCRAPER_HEADLESS")
        if headless_env is not None:
            config.scraper.headless = headless_env.lower() in ("true", "1", "yes")

        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if bot_token:
            config.telegram.bot_token = bot_token

        admin_ids = os.getenv("TELEGRAM_ADMIN_IDS", "").strip()
        if admin_ids:
            try:
                config.telegram.admin_chat_ids = [int(x.strip()) for x in admin_ids.split(",") if x.strip()]
            except ValueError:
                pass

        notif_int = os.getenv("NOTIFICATION_INTERVAL_MIN", "").strip()
        if notif_int and notif_int.isdigit():
            config.telegram.notification_interval_min = int(notif_int)

        proxy_str = os.getenv("DEFAULT_PROXY", "").strip()
        if proxy_str:
            config.proxy.enabled = True
            config.proxy.default_proxy = proxy_str

        proxy_enabled = os.getenv("PROXY_ENABLED", "").strip()
        if proxy_enabled:
            config.proxy.enabled = proxy_enabled.lower() in ("true", "1", "yes")

        web_host = os.getenv("WEB_HOST", "").strip()
        if web_host:
            config.web.host = web_host

        web_port = os.getenv("WEB_PORT", "").strip()
        if web_port and web_port.isdigit():
            config.web.port = int(web_port)

        return config

    def save_to_env(self) -> None:
        """Сохранение ключевых параметров в .env файл"""
        env_lines = []
        admin_ids_str = ",".join(str(i) for i in self.telegram.admin_chat_ids)
        
        env_lines.append(f"# Конфигурация Telegram")
        env_lines.append(f"TELEGRAM_BOT_TOKEN={self.telegram.bot_token}")
        env_lines.append(f"TELEGRAM_ADMIN_IDS={admin_ids_str}")
        env_lines.append(f"NOTIFICATION_INTERVAL_MIN={self.telegram.notification_interval_min}\n")

        env_lines.append(f"# Конфигурация парсера")
        env_lines.append(f"SCRAPER_HEADLESS={'true' if self.scraper.headless else 'false'}")
        env_lines.append(f"SCRAPER_TIMEOUT_MS={self.scraper.timeout_ms}")
        env_lines.append(f"SCRAPER_PAGE_DELAY_MIN={self.scraper.page_delay_min}")
        env_lines.append(f"SCRAPER_PAGE_DELAY_MAX={self.scraper.page_delay_max}\n")

        env_lines.append(f"# Конфигурация прокси")
        env_lines.append(f"PROXY_ENABLED={'true' if self.proxy.enabled else 'false'}")
        if self.proxy.default_proxy:
            env_lines.append(f"DEFAULT_PROXY={self.proxy.default_proxy}")
        env_lines.append("")

        env_lines.append(f"# Конфигурация веб-панели")
        env_lines.append(f"WEB_HOST={self.web.host}")
        env_lines.append(f"WEB_PORT={self.web.port}\n")

        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(env_lines))


config = AppConfig.load()
