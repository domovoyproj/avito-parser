import hashlib
import json
import secrets
import os
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import aiosqlite
from config import config
from models import (
    AISettings,
    AuditLogEntry,
    AvitoItem,
    CustomScoringRule,
    PriceChange,
    PriceStats,
    SearchQuery,
    SellerBlacklist,
    SellerInfo,
    SystemTelemetry,
    TelegramChatSettings,
    User,
    UserRole,
    WebhookSettings,
)
from ai_scoring import deal_scoring_engine
from repositories.items import ItemsRepository
from repositories.users import UsersRepository
from repositories.searches import SearchesRepository
from repositories.settings import SettingsRepository
from repositories.analytics import AnalyticsRepository
from repositories.monitoring_runs import MonitoringRunsRepository
from repositories.watchlists import WatchlistsRepository


SCHEMA_VERSION = 8


class Database(
    ItemsRepository,
    UsersRepository,
    SearchesRepository,
    SettingsRepository,
    AnalyticsRepository,
    MonitoringRunsRepository,
    WatchlistsRepository,
):
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or config.db_path

    @asynccontextmanager
    async def connection(self):
        async with aiosqlite.connect(self.db_path, timeout=30) as connection:
            await connection.execute("PRAGMA foreign_keys=ON")
            await connection.execute("PRAGMA busy_timeout=30000")
            try:
                yield connection
            except BaseException:
                await connection.rollback()
                raise

    async def backup(self, destination: Path) -> Path:
        destination = Path(destination)
        if destination.resolve() == self.db_path.resolve():
            raise ValueError("Backup cannot overwrite the active database")
        destination.parent.mkdir(parents=True, exist_ok=True)
        async with self.connection() as source:
            with closing(sqlite3.connect(destination)) as target:
                await source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Backup integrity check failed")
        return destination

    async def init_db(self) -> None:
        """Инициализация таблиц базы данных SQLite"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with self.connection() as db:
            exists = await (
                await db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
                )
            ).fetchone()
            old_version = 0
            if exists:
                version = await (
                    await db.execute("SELECT MAX(version) FROM schema_migrations")
                ).fetchone()
                old_version = int(version[0] or 0) if version else 0
                if version and version[0] and version[0] > SCHEMA_VERSION:
                    raise ValueError("Database schema is newer than this application")
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("BEGIN IMMEDIATE")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    price INTEGER,
                    old_price INTEGER,
                    price_string TEXT,
                    url TEXT NOT NULL,
                    address TEXT,
                    metro TEXT,
                    published_at TEXT,
                    description TEXT,
                    main_image TEXT,
                    images_json TEXT,
                    params_json TEXT,
                    seller_json TEXT,
                    delivery_available BOOLEAN DEFAULT 0,
                    is_vip BOOLEAN DEFAULT 0,
                    category TEXT,
                    search_query_id INTEGER,
                    is_favorite BOOLEAN DEFAULT 0,
                    is_hidden BOOLEAN DEFAULT 0,
                    is_reserved BOOLEAN DEFAULT 0,
                    is_closed BOOLEAN DEFAULT 0,
                    market_avg_price INTEGER,
                    is_hot_deal BOOLEAN DEFAULT 0,
                    deal_score INTEGER DEFAULT 50,
                    deal_grade TEXT DEFAULT 'FAIR',
                    deal_reasons_json TEXT,
                    ai_summary TEXT,
                    detected_flaws_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Проверка и добавление новых колонок (миграция)
            cursor = await db.execute("PRAGMA table_info(items)")
            columns = [row[1] for row in await cursor.fetchall()]
            if "is_favorite" not in columns:
                await db.execute(
                    "ALTER TABLE items ADD COLUMN is_favorite BOOLEAN DEFAULT 0"
                )
            if "is_hidden" not in columns:
                await db.execute(
                    "ALTER TABLE items ADD COLUMN is_hidden BOOLEAN DEFAULT 0"
                )
            if "market_avg_price" not in columns:
                await db.execute(
                    "ALTER TABLE items ADD COLUMN market_avg_price INTEGER"
                )
            if "is_hot_deal" not in columns:
                await db.execute(
                    "ALTER TABLE items ADD COLUMN is_hot_deal BOOLEAN DEFAULT 0"
                )
            if "deal_score" not in columns:
                await db.execute(
                    "ALTER TABLE items ADD COLUMN deal_score INTEGER DEFAULT 50"
                )
            if "deal_grade" not in columns:
                await db.execute(
                    "ALTER TABLE items ADD COLUMN deal_grade TEXT DEFAULT 'FAIR'"
                )
            if "deal_reasons_json" not in columns:
                await db.execute("ALTER TABLE items ADD COLUMN deal_reasons_json TEXT")
            if "ai_summary" not in columns:
                await db.execute("ALTER TABLE items ADD COLUMN ai_summary TEXT")
            if "detected_flaws_json" not in columns:
                await db.execute(
                    "ALTER TABLE items ADD COLUMN detected_flaws_json TEXT"
                )
            if "is_reserved" not in columns:
                await db.execute(
                    "ALTER TABLE items ADD COLUMN is_reserved BOOLEAN DEFAULT 0"
                )
            if "is_closed" not in columns:
                await db.execute(
                    "ALTER TABLE items ADD COLUMN is_closed BOOLEAN DEFAULT 0"
                )
            if "content_hash" not in columns:
                await db.execute("ALTER TABLE items ADD COLUMN content_hash TEXT")
            if "closed_at" not in columns:
                await db.execute("ALTER TABLE items ADD COLUMN closed_at TIMESTAMP")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS searches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    min_price INTEGER,
                    max_price INTEGER,
                    check_interval_min INTEGER DEFAULT 10,
                    enabled BOOLEAN DEFAULT 1,
                    last_checked_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Миграция searches: активные часы мониторинга
            cursor_s = await db.execute("PRAGMA table_info(searches)")
            search_cols = [row[1] for row in await cursor_s.fetchall()]
            if "active_hours_start" not in search_cols:
                await db.execute(
                    "ALTER TABLE searches ADD COLUMN active_hours_start INTEGER DEFAULT 0"
                )
            if "active_hours_end" not in search_cols:
                await db.execute(
                    "ALTER TABLE searches ADD COLUMN active_hours_end INTEGER DEFAULT 24"
                )

            await db.execute("""
                CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id TEXT NOT NULL,
                    old_price INTEGER NOT NULL,
                    new_price INTEGER NOT NULL,
                    delta INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS sent_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id TEXT NOT NULL,
                    search_query_id INTEGER,
                    notification_type TEXT NOT NULL,
                    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(item_id, search_query_id, notification_type)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'operator',
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login_at TIMESTAMP
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_sessions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS telegram_chat_settings (
                    chat_id INTEGER PRIMARY KEY,
                    notify_new BOOLEAN NOT NULL DEFAULT 1,
                    notify_drops BOOLEAN NOT NULL DEFAULT 1,
                    send_photos BOOLEAN NOT NULL DEFAULT 1,
                    delivery_only BOOLEAN NOT NULL DEFAULT 0,
                    min_discount_pct INTEGER NOT NULL DEFAULT 0,
                    min_deal_score INTEGER NOT NULL DEFAULT 0,
                    keyword_filter TEXT,
                    blacklisted_sellers_json TEXT,
                    quiet_hours_enabled BOOLEAN NOT NULL DEFAULT 0,
                    quiet_hours_start INTEGER NOT NULL DEFAULT 23,
                    quiet_hours_end INTEGER NOT NULL DEFAULT 7,
                    only_below_market BOOLEAN NOT NULL DEFAULT 0,
                    below_market_pct INTEGER NOT NULL DEFAULT 15,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Проверка и добавление колонок в telegram_chat_settings (миграция)
            cursor_tg = await db.execute("PRAGMA table_info(telegram_chat_settings)")
            tg_cols = [row[1] for row in await cursor_tg.fetchall()]
            if "only_below_market" not in tg_cols:
                await db.execute(
                    "ALTER TABLE telegram_chat_settings ADD COLUMN only_below_market BOOLEAN DEFAULT 0"
                )
            if "below_market_pct" not in tg_cols:
                await db.execute(
                    "ALTER TABLE telegram_chat_settings ADD COLUMN below_market_pct INTEGER DEFAULT 15"
                )
            if "min_deal_score" not in tg_cols:
                await db.execute(
                    "ALTER TABLE telegram_chat_settings ADD COLUMN min_deal_score INTEGER DEFAULT 0"
                )

            await db.execute("""
                CREATE TABLE IF NOT EXISTS ai_settings (
                    id INTEGER PRIMARY KEY,
                    enabled BOOLEAN NOT NULL DEFAULT 0,
                    provider TEXT NOT NULL DEFAULT 'deepseek',
                    api_key TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT 'deepseek-chat',
                    api_base TEXT NOT NULL DEFAULT 'https://api.deepseek.com',
                    prompt_template TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS seller_blacklist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    seller_name TEXT UNIQUE NOT NULL,
                    reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT,
                    action TEXT NOT NULL,
                    details TEXT,
                    ip_address TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS custom_scoring_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern TEXT NOT NULL,
                    label TEXT NOT NULL,
                    score_delta INTEGER NOT NULL DEFAULT -25,
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS webhook_settings (
                    id INTEGER PRIMARY KEY,
                    url TEXT NOT NULL DEFAULT '',
                    enabled BOOLEAN NOT NULL DEFAULT 0,
                    send_new BOOLEAN NOT NULL DEFAULT 1,
                    send_drops BOOLEAN NOT NULL DEFAULT 1,
                    min_deal_score INTEGER NOT NULL DEFAULT 0,
                    secret TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Индексы для ускорения поиска
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_items_deal_score ON items(deal_score)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_items_deal_grade ON items(deal_grade)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_items_search_id ON items(search_query_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_items_price ON items(price)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_items_created_at ON items(created_at)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_items_favorite ON items(is_favorite)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_notif_lookup ON sent_notifications(item_id, search_query_id, notification_type)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_price_history_item ON price_history(item_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_token ON user_sessions(token)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_items_content_hash ON items(content_hash)"
            )

            await db.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            cursor = await db.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            )
            if (await cursor.fetchone())[0] > SCHEMA_VERSION:
                raise RuntimeError("Database schema is newer than this application")
            await db.execute(
                "CREATE TABLE IF NOT EXISTS item_searches (item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE, search_id INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE, last_seen_at TEXT NOT NULL, PRIMARY KEY(item_id, search_id))"
            )
            await db.execute(
                "INSERT OR IGNORE INTO item_searches SELECT id, search_query_id, updated_at FROM items WHERE search_query_id IN (SELECT id FROM searches)"
            )
            await db.execute(
                "CREATE TABLE IF NOT EXISTS work_leases (name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at REAL NOT NULL)"
            )
            await db.execute(
                "CREATE TABLE IF NOT EXISTS notification_outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT UNIQUE NOT NULL, channel TEXT NOT NULL, destination TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, available_at REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0, lease_token TEXT, last_error TEXT)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_outbox_pending ON notification_outbox(status, available_at)"
            )
            outbox_columns = {row[1] for row in await (await db.execute("PRAGMA table_info(notification_outbox)")).fetchall()}
            if 'created_at' not in outbox_columns:
                await db.execute("ALTER TABLE notification_outbox ADD COLUMN created_at REAL NOT NULL DEFAULT 0")
                # Historical enqueue times are unavailable; migration time is the documented baseline.
                await db.execute("UPDATE notification_outbox SET created_at=?", (datetime.now().timestamp(),))
            await db.execute("CREATE INDEX IF NOT EXISTS idx_memberships_search ON item_searches(search_id,item_id)")
            await db.execute(
                "CREATE TABLE IF NOT EXISTS llm_cache (cache_key TEXT PRIMARY KEY, verdict TEXT NOT NULL, expires_at REAL NOT NULL)"
            )
            await db.execute(
                "CREATE TABLE IF NOT EXISTS llm_budget (day TEXT PRIMARY KEY, requests INTEGER NOT NULL)"
            )
            columns = {
                row[1]
                for row in await (
                    await db.execute("PRAGMA table_info(items)")
                ).fetchall()
            }
            if "score_version" not in columns:
                await db.execute(
                    "ALTER TABLE items ADD COLUMN score_version TEXT DEFAULT 'legacy-1'"
                )
            if "score_confidence" not in columns:
                await db.execute(
                    "ALTER TABLE items ADD COLUMN score_confidence REAL DEFAULT 0"
                )
            await db.execute(
                "CREATE TABLE IF NOT EXISTS export_jobs (id TEXT PRIMARY KEY, format TEXT NOT NULL, filters TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL, finished_at REAL, rows_written INTEGER DEFAULT 0, error TEXT)"
            )
            await db.execute(
                "CREATE TABLE IF NOT EXISTS monitoring_runs (run_id TEXT PRIMARY KEY, search_id INTEGER REFERENCES searches(id) ON DELETE SET NULL, search_name TEXT NOT NULL, started_at REAL NOT NULL, finished_at REAL NOT NULL, duration_seconds REAL NOT NULL, outcome TEXT NOT NULL, engine TEXT, found_count INTEGER NOT NULL DEFAULT 0, new_count INTEGER NOT NULL DEFAULT 0, drops_count INTEGER NOT NULL DEFAULT 0, error_type TEXT)"
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_monitoring_runs_time ON monitoring_runs(started_at DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_monitoring_runs_search_time ON monitoring_runs(search_id, started_at DESC)")
            await db.execute("CREATE TABLE IF NOT EXISTS watchlists (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, name TEXT NOT NULL, is_default INTEGER NOT NULL DEFAULT 0, alerts_paused INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, UNIQUE(user_id,name))")
            await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_watchlists_default ON watchlists(user_id) WHERE is_default=1")
            await db.execute("CREATE TABLE IF NOT EXISTS watchlist_items (watchlist_id INTEGER NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE, item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE, target_price INTEGER, note TEXT NOT NULL DEFAULT '', alerts_paused INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, PRIMARY KEY(watchlist_id,item_id))")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_items_item ON watchlist_items(item_id)")
            await db.execute("CREATE TABLE IF NOT EXISTS telegram_link_challenges (code_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at REAL NOT NULL)")
            await db.execute("CREATE TABLE IF NOT EXISTS user_telegram_chats (user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, chat_id INTEGER NOT NULL UNIQUE, created_at REAL NOT NULL, PRIMARY KEY(user_id,chat_id))")
            if old_version < 8:
                await db.execute("INSERT OR IGNORE INTO watchlists(user_id,name,is_default,created_at) SELECT id,'Избранное',1,strftime('%s','now') FROM users")
                await db.execute("INSERT OR IGNORE INTO watchlist_items(watchlist_id,item_id,created_at) SELECT w.id,i.id,strftime('%s','now') FROM watchlists w JOIN items i ON i.is_favorite=1 WHERE w.is_default=1")
            for version in range(1, SCHEMA_VERSION + 1):
                await db.execute(
                    "INSERT OR IGNORE INTO schema_migrations VALUES (?, ?)",
                    (version, datetime.now().isoformat()),
                )
            await db.commit()

        # Инициализация дефолтного админа при первом старте
        await self.init_default_admin()

    # Методы работы с поисковыми запросами (мониторингом)

    # Методы работы с историей цен и падениями цен

    # Методы работы с историей уведомлений

    # АВТОРИЗАЦИЯ И ПОЛЬЗОВАТЕЛИ (PBKDF2-SHA256)
    # ==========================================================================

    # ==========================================================================
    # СЕССИИ ПОЛЬЗОВАТЕЛЕЙ
    # ==========================================================================

    # ==========================================================================
    # НАСТРОЙКИ УВЕДОМЛЕНИЙ TELEGRAM ДЛЯ ЧАТОВ
    # ==========================================================================

    # ==========================================================================
    # АНАЛИТИКА ЦЕН, ИЗБРАННОЕ И МАССОВЫЕ ДЕЙСТВИЯ
    # ==========================================================================

    # ==========================================================================
    # ЧЕРНЫЙ СПИСОК ПРОДАВЦОВ
    # ==========================================================================

    # ==========================================================================
    # ЖУРНАЛ АУДИТА ДЕЙСТВИЙ (AUDIT LOG)
    # ==========================================================================

    # ==========================================================================
    # СИСТЕМНАЯ ТЕЛЕМЕТРИЯ
    # ==========================================================================

    # ==========================================================================
    # ДЕДУПЛИКАЦИЯ И ХЕШИРОВАНИЕ КОНТЕНТА
    # ==========================================================================

    # ==========================================================================
    # ПОЛЬЗОВАТЕЛЬСКИЕ ПРАВИЛА СКОРИНГА (СТОП-СЛОВА И БУСТЕРЫ)
    # ==========================================================================

    # ==========================================================================
    # WEBHOOK НАСТРОЙКИ
    # ==========================================================================

    # ==========================================================================
    # СТАТИСТИКА СКОРОСТИ РЫНКА (GEM/HOT LIFETIME)
    # ==========================================================================


db = Database()
