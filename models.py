from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ExportFormat(str, Enum):
    EXCEL = "excel"
    CSV = "csv"
    JSON = "json"
    HTML = "html"


class SellerInfo(BaseModel):
    name: Optional[str] = Field(default=None, description="Имя продавца или компании")
    rating: Optional[float] = Field(default=None, description="Рейтинг продавца (например 4.9)")
    reviews_count: Optional[int] = Field(default=None, description="Количество отзывов")
    seller_type: Optional[str] = Field(default=None, description="Частное лицо / Компания / Агентство")
    profile_url: Optional[str] = Field(default=None, description="Ссылка на профиль")
    is_verified: bool = Field(default=False, description="Проверен ли профиль документами/госуслугами")


class AvitoItem(BaseModel):
    id: str = Field(description="Уникальный идентификатор объявления на Авито")
    title: str = Field(description="Заголовок объявления")
    price: Optional[int] = Field(default=None, description="Текущая цена в рублях")
    price_string: Optional[str] = Field(default=None, description="Исходная строка цены (например '15 000 ₽')")
    old_price: Optional[int] = Field(default=None, description="Предыдущая зафиксированная цена")
    url: str = Field(description="Полная ссылка на объявление")
    address: Optional[str] = Field(default=None, description="Адрес или город/район")
    metro: Optional[str] = Field(default=None, description="Станция метро (если есть)")
    published_at: Optional[str] = Field(default=None, description="Дата/время публикации на Авито")
    
    # Детальные данные
    description: Optional[str] = Field(default=None, description="Полный текст описания")
    images: List[str] = Field(default_factory=list, description="Список прямых ссылок на фотографии")
    main_image: Optional[str] = Field(default=None, description="Главное фото объявления")
    params: Dict[str, str] = Field(default_factory=dict, description="Характеристики товара/авто/недвижимости")
    seller: Optional[SellerInfo] = Field(default=None, description="Информация о продавце")
    
    # Флаги & Аналитика
    delivery_available: bool = Field(default=False, description="Доступна ли Авито Доставка")
    is_vip: bool = Field(default=False, description="Платное/VIP продвижение")
    category: Optional[str] = Field(default=None, description="Категория")
    is_favorite: bool = Field(default=False, description="Добавлено ли в избранное")
    is_hidden: bool = Field(default=False, description="Скрыто ли пользователем")
    is_reserved: bool = Field(default=False, description="Товар зарезервирован другим покупателем")
    is_closed: bool = Field(default=False, description="Объявление закрыто / снято с публикации / продано")
    market_avg_price: Optional[int] = Field(default=None, description="Средняя рыночная цена аналогов")
    is_hot_deal: bool = Field(default=False, description="Флаг горячего предложения (цена ниже рынка на 15%+)")
    deal_score: int = Field(default=50, description="Скоринг сделки 0-100")
    deal_grade: str = Field(default="FAIR", description="GEM | HOT | FAIR | CAUTION")
    deal_reasons: List[str] = Field(default_factory=list, description="Факторы оценки")
    ai_summary: Optional[str] = Field(default=None, description="Краткий вердикт ИИ")
    detected_flaws: List[str] = Field(default_factory=list, description="Выявленные риски/дефекты")
    content_hash: Optional[str] = Field(default=None, description='Хеш контента для дедупликации')
    closed_at: Optional[datetime] = Field(default=None, description='Время когда объявление было помечено как закрытое/проданное')
    # Метаданные парсера
    search_query_id: Optional[int] = Field(default=None, description="ID поискового запроса, если найдено в мониторинге")
    created_at: datetime = Field(default_factory=datetime.now, description="Время добавления в БД")
    updated_at: datetime = Field(default_factory=datetime.now, description="Время последнего обновления")


class SearchQuery(BaseModel):
    id: Optional[int] = Field(default=None, description="ID в локальной БД")
    name: str = Field(description="Название поиска для удобства (например 'iPhone 15 Pro Москва')")
    url: str = Field(description="Полная ссылка на поисковую выдачу Авито со всеми фильтрами")
    min_price: Optional[int] = Field(default=None, description="Минимальная цена фильтра")
    max_price: Optional[int] = Field(default=None, description="Максимальная цена фильтра")
    check_interval_min: int = Field(default=10, description="Интервал проверки в минутах")
    enabled: bool = Field(default=True, description="Активен ли мониторинг")
    active_hours_start: int = Field(default=0, description='Начало активных часов мониторинга (0-23)')
    active_hours_end: int = Field(default=24, description='Конец активных часов (1-24, 24 = круглосуточно)')
    last_checked_at: Optional[datetime] = Field(default=None, description="Время последней проверки")
    created_at: datetime = Field(default_factory=datetime.now)


class PriceChange(BaseModel):
    item_id: str
    item_title: str
    old_price: int
    new_price: int
    delta: int
    discount_pct: float = 0.0
    url: str
    changed_at: datetime = Field(default_factory=datetime.now)


class PriceStats(BaseModel):
    item_count: int = 0
    min_price: Optional[int] = None
    max_price: Optional[int] = None
    avg_price: Optional[int] = None
    median_price: Optional[int] = None
    total_valuation: int = 0


class ParseResult(BaseModel):
    items: List[AvitoItem] = Field(default_factory=list)
    total_found: int = 0
    new_items_count: int = 0
    price_dropped_count: int = 0
    elapsed_seconds: float = 0.0
    errors: List[str] = Field(default_factory=list)


class UserRole(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class User(BaseModel):
    id: int
    username: str
    role: UserRole = UserRole.VIEWER
    is_active: bool = True
    created_at: Optional[datetime] = None
    last_login_at: Optional[datetime] = None


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=4, max_length=100)
    role: UserRole = UserRole.OPERATOR


class UserUpdate(BaseModel):
    username: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=4)


class TelegramChatSettings(BaseModel):
    chat_id: int
    notify_new: bool = True
    notify_drops: bool = True
    send_photos: bool = True
    delivery_only: bool = False
    min_discount_pct: int = 0
    min_deal_score: int = 0
    only_below_market: bool = False
    below_market_pct: int = 15
    keyword_filter: Optional[str] = None
    blacklisted_sellers: List[str] = Field(default_factory=list)
    quiet_hours_enabled: bool = False
    quiet_hours_start: int = 23
    quiet_hours_end: int = 7
    updated_at: Optional[datetime] = None


class AISettings(BaseModel):
    enabled: bool = False
    provider: str = "deepseek"  # deepseek | openai | openrouter | ollama
    api_key: str = ""
    model: str = "deepseek-chat"
    api_base: str = "https://api.deepseek.com"
    prompt_template: Optional[str] = None
    updated_at: Optional[datetime] = None


class SellerBlacklist(BaseModel):
    id: Optional[int] = None
    seller_name: str
    reason: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)


class AuditLogEntry(BaseModel):
    id: Optional[int] = None
    user_id: Optional[int] = None
    username: Optional[str] = None
    action: str
    details: Optional[str] = None
    ip_address: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)


class BatchItemsActionRequest(BaseModel):
    item_ids: List[str]
    action: str = Field(description="delete | favorite | unfavorite | assign_search")
    search_query_id: Optional[int] = None


class SystemTelemetry(BaseModel):
    total_items: int = 0
    total_searches: int = 0
    active_searches: int = 0
    total_price_drops: int = 0
    db_size_kb: int = 0
    proxy_count: int = 0
    active_proxies: int = 0
    telegram_bot_active: bool = False
    uptime_seconds: int = 0
    server_time: str = ""


class CustomScoringRule(BaseModel):
    id: Optional[int] = None
    pattern: str = Field(description='Regex паттерн для поиска в тексте')
    label: str = Field(description='Описание правила')
    score_delta: int = Field(description='Изменение скоринга (+ бустер, - штраф)')
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.now)


class WebhookSettings(BaseModel):
    id: Optional[int] = None
    url: str = Field(default='', description='Webhook URL для отправки уведомлений')
    enabled: bool = False
    send_new: bool = True
    send_drops: bool = True
    min_deal_score: int = 0
    secret: str = Field(default='', description='Secret для подписи payload')
    updated_at: Optional[datetime] = None
