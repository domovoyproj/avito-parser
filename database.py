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
    AISettings, AuditLogEntry, AvitoItem, CustomScoringRule, PriceChange, PriceStats, SearchQuery,
    SellerBlacklist, SellerInfo, SystemTelemetry, TelegramChatSettings, User, UserRole,
    WebhookSettings
)
from ai_scoring import deal_scoring_engine
class Database:
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
            exists = await (await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'")).fetchone()
            if exists:
                version = await (await db.execute('SELECT MAX(version) FROM schema_migrations')).fetchone()
                if version and version[0] and version[0] > 4:
                    raise ValueError('Database schema is newer than this application')
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute('BEGIN IMMEDIATE')
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
                await db.execute("ALTER TABLE items ADD COLUMN is_favorite BOOLEAN DEFAULT 0")
            if "is_hidden" not in columns:
                await db.execute("ALTER TABLE items ADD COLUMN is_hidden BOOLEAN DEFAULT 0")
            if "market_avg_price" not in columns:
                await db.execute("ALTER TABLE items ADD COLUMN market_avg_price INTEGER")
            if "is_hot_deal" not in columns:
                await db.execute("ALTER TABLE items ADD COLUMN is_hot_deal BOOLEAN DEFAULT 0")
            if "deal_score" not in columns:
                await db.execute("ALTER TABLE items ADD COLUMN deal_score INTEGER DEFAULT 50")
            if "deal_grade" not in columns:
                await db.execute("ALTER TABLE items ADD COLUMN deal_grade TEXT DEFAULT 'FAIR'")
            if "deal_reasons_json" not in columns:
                await db.execute("ALTER TABLE items ADD COLUMN deal_reasons_json TEXT")
            if "ai_summary" not in columns:
                await db.execute("ALTER TABLE items ADD COLUMN ai_summary TEXT")
            if "detected_flaws_json" not in columns:
                await db.execute("ALTER TABLE items ADD COLUMN detected_flaws_json TEXT")
            if "is_reserved" not in columns:
                await db.execute("ALTER TABLE items ADD COLUMN is_reserved BOOLEAN DEFAULT 0")
            if "is_closed" not in columns:
                await db.execute("ALTER TABLE items ADD COLUMN is_closed BOOLEAN DEFAULT 0")
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
                await db.execute("ALTER TABLE searches ADD COLUMN active_hours_start INTEGER DEFAULT 0")
            if "active_hours_end" not in search_cols:
                await db.execute("ALTER TABLE searches ADD COLUMN active_hours_end INTEGER DEFAULT 24")

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
                await db.execute("ALTER TABLE telegram_chat_settings ADD COLUMN only_below_market BOOLEAN DEFAULT 0")
            if "below_market_pct" not in tg_cols:
                await db.execute("ALTER TABLE telegram_chat_settings ADD COLUMN below_market_pct INTEGER DEFAULT 15")
            if "min_deal_score" not in tg_cols:
                await db.execute("ALTER TABLE telegram_chat_settings ADD COLUMN min_deal_score INTEGER DEFAULT 0")

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
            await db.execute("CREATE INDEX IF NOT EXISTS idx_items_deal_score ON items(deal_score)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_items_deal_grade ON items(deal_grade)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_items_search_id ON items(search_query_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_items_price ON items(price)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_items_created_at ON items(created_at)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_items_favorite ON items(is_favorite)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_notif_lookup ON sent_notifications(item_id, search_query_id, notification_type)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_price_history_item ON price_history(item_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_token ON user_sessions(token)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_items_content_hash ON items(content_hash)")

            await db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            cursor = await db.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")
            if (await cursor.fetchone())[0] > 4:
                raise RuntimeError("Database schema is newer than this application")
            await db.execute("CREATE TABLE IF NOT EXISTS item_searches (item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE, search_id INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE, last_seen_at TEXT NOT NULL, PRIMARY KEY(item_id, search_id))")
            await db.execute("INSERT OR IGNORE INTO item_searches SELECT id, search_query_id, updated_at FROM items WHERE search_query_id IN (SELECT id FROM searches)")
            await db.execute("CREATE TABLE IF NOT EXISTS work_leases (name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at REAL NOT NULL)")
            await db.execute("CREATE TABLE IF NOT EXISTS notification_outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT UNIQUE NOT NULL, channel TEXT NOT NULL, destination TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, available_at REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0, lease_token TEXT, last_error TEXT)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_outbox_pending ON notification_outbox(status, available_at)")
            await db.execute('CREATE TABLE IF NOT EXISTS llm_cache (cache_key TEXT PRIMARY KEY, verdict TEXT NOT NULL, expires_at REAL NOT NULL)')
            await db.execute('CREATE TABLE IF NOT EXISTS llm_budget (day TEXT PRIMARY KEY, requests INTEGER NOT NULL)')
            columns = {row[1] for row in await (await db.execute('PRAGMA table_info(items)')).fetchall()}
            if 'score_version' not in columns:
                await db.execute("ALTER TABLE items ADD COLUMN score_version TEXT DEFAULT 'legacy-1'")
            if 'score_confidence' not in columns:
                await db.execute('ALTER TABLE items ADD COLUMN score_confidence REAL DEFAULT 0')
            for version in (1, 2, 3, 4):
                await db.execute("INSERT OR IGNORE INTO schema_migrations VALUES (?, ?)", (version, datetime.now().isoformat()))
            await db.commit()

        # Инициализация дефолтного админа при первом старте
        await self.init_default_admin()

    async def save_item(self, item: AvitoItem) -> Tuple[AvitoItem, bool, bool, Optional[int]]:
        """
        Сохраняет или обновляет объявление.
        Возвращает (item, is_new, is_price_drop, previous_price).
        """
        is_new = False
        is_price_drop = False
        prev_price = None

        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute("SELECT id, price, old_price, ai_summary FROM items WHERE id = ?", (item.id,))
            row = await cursor.fetchone()

            # Автоматический расчет скоринга сделки
            market_med, market_avg = await self.get_search_market_stats(item.search_query_id)
            deal_score, deal_grade, deal_reasons, detected_flaws = deal_scoring_engine.evaluate_item(
                item, market_median=market_med, market_avg=market_avg
            )
            item.deal_score = deal_score
            item.deal_grade = deal_grade
            item.deal_reasons = deal_reasons
            item.detected_flaws = detected_flaws
            if deal_grade in ("GEM", "HOT"):
                item.is_hot_deal = True

            images_json = json.dumps(item.images, ensure_ascii=False)
            params_json = json.dumps(item.params, ensure_ascii=False)
            seller_json = json.dumps(item.seller.model_dump() if item.seller else None, ensure_ascii=False)
            deal_reasons_json = json.dumps(item.deal_reasons, ensure_ascii=False)
            detected_flaws_json = json.dumps(item.detected_flaws, ensure_ascii=False)
            content_hash = await self.compute_content_hash(item)
            item.content_hash = content_hash
            now = datetime.now()

            if row is None:
                is_new = True
                await db.execute("""
                    INSERT INTO items (
                        id, title, price, old_price, price_string, url, address, metro,
                        published_at, description, main_image, images_json, params_json,
                        seller_json, delivery_available, is_vip, category, search_query_id,
                        deal_score, deal_grade, deal_reasons_json, ai_summary, detected_flaws_json,
                        is_hot_deal, is_reserved, is_closed, content_hash, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    item.id, item.title, item.price, item.old_price, item.price_string,
                    item.url, item.address, item.metro, item.published_at, item.description,
                    item.main_image, images_json, params_json, seller_json,
                    item.delivery_available, item.is_vip, item.category, item.search_query_id,
                    item.deal_score, item.deal_grade, deal_reasons_json, item.ai_summary,
                    detected_flaws_json, 1 if item.is_hot_deal else 0,
                    1 if item.is_reserved else 0, 1 if item.is_closed else 0, content_hash, now, now
                ))
            else:
                prev_price = row["price"]
                existing_ai_summary = row["ai_summary"]
                if existing_ai_summary and not item.ai_summary:
                    item.ai_summary = existing_ai_summary

                # Проверка изменения цены
                if item.price is not None and prev_price is not None and item.price != prev_price:
                    delta = item.price - prev_price
                    item.old_price = prev_price
                    if delta < 0:
                        is_price_drop = True

                    await db.execute("""
                        INSERT INTO price_history (item_id, old_price, new_price, delta, created_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (item.id, prev_price, item.price, delta, now))

                await db.execute("""
                    UPDATE items SET
                        title = ?,
                        price = ?,
                        old_price = COALESCE(?, old_price),
                        price_string = ?,
                        url = ?,
                        address = COALESCE(?, address),
                        metro = COALESCE(?, metro),
                        published_at = COALESCE(?, published_at),
                        description = COALESCE(?, description),
                        main_image = COALESCE(?, main_image),
                        images_json = ?,
                        params_json = ?,
                        seller_json = ?,
                        delivery_available = ?,
                        is_vip = ?,
                        category = COALESCE(?, category),
                        deal_score = ?,
                        deal_grade = ?,
                        deal_reasons_json = ?,
                        ai_summary = COALESCE(?, ai_summary),
                        detected_flaws_json = ?,
                        is_hot_deal = ?,
                        is_reserved = ?,
                        is_closed = ?,
                        content_hash = ?,
                        updated_at = ?
                    WHERE id = ?
                """, (
                    item.title, item.price, item.old_price, item.price_string, item.url,
                    item.address, item.metro, item.published_at, item.description,
                    item.main_image, images_json, params_json, seller_json,
                    item.delivery_available, item.is_vip, item.category,
                    item.deal_score, item.deal_grade, deal_reasons_json, item.ai_summary,
                    detected_flaws_json, 1 if item.is_hot_deal else 0,
                    1 if item.is_reserved else 0, 1 if item.is_closed else 0, content_hash, now, item.id
                ))
            await db.execute('UPDATE items SET score_version=?,score_confidence=? WHERE id=?', (item.score_version, item.score_confidence, item.id))
            if item.search_query_id is not None:
                await db.execute("INSERT INTO item_searches VALUES (?, ?, ?) ON CONFLICT(item_id, search_id) DO UPDATE SET last_seen_at=excluded.last_seen_at", (item.id, item.search_query_id, now.isoformat()))
            if is_new or is_price_drop:
                search_name = "Поиск"
                if item.search_query_id is not None:
                    search_row = await (await db.execute("SELECT name FROM searches WHERE id=?", (item.search_query_id,))).fetchone()
                    if search_row:
                        search_name = search_row[0]
                event_type = "new" if is_new else "price_drop"
                payload = json.dumps({"item": item.model_dump(mode="json"), "search_name": search_name, "type": event_type}, ensure_ascii=False)
                event = f"{item.id}:{item.search_query_id}:{event_type}:{now.isoformat()}"
                if config.telegram.bot_token:
                    for chat in set(config.telegram.admin_chat_ids):
                        await db.execute("INSERT OR IGNORE INTO notification_outbox(event_key, channel, destination, payload) VALUES (?, 'telegram', ?, ?)", (f"{event}:telegram:{chat}", str(chat), payload))
                webhook = await (await db.execute("SELECT url, enabled, send_new, send_drops FROM webhook_settings WHERE id=1")).fetchone()
                if webhook and webhook[1] and webhook[0] and webhook[2 if is_new else 3]:
                    await db.execute("INSERT OR IGNORE INTO notification_outbox(event_key, channel, destination, payload) VALUES (?, 'webhook', ?, ?)", (f"{event}:webhook", webhook[0], payload))
            await db.commit()
        return item, is_new, is_price_drop, prev_price

    async def save_items(self, items: List[AvitoItem]) -> Dict[str, Any]:
        """Пакетное сохранение объявлений с подсчетом статистики"""
        new_count = 0
        price_drop_count = 0
        price_changes: List[PriceChange] = []
        new_items: List[AvitoItem] = []

        for item in items:
            saved_item, is_new, is_price_drop, prev_price = await self.save_item(item)
            if is_new:
                new_count += 1
                new_items.append(saved_item)
            elif is_price_drop and prev_price is not None and item.price is not None:
                price_drop_count += 1
                price_changes.append(PriceChange(
                    item_id=item.id,
                    item_title=item.title,
                    old_price=prev_price,
                    new_price=item.price,
                    delta=item.price - prev_price,
                    url=item.url
                ))

        return {
            "total": len(items),
            "new_count": new_count,
            "new_items": new_items,
            "price_drop_count": price_drop_count,
            "price_changes": price_changes
        }

    async def cleanup_stale_items(self, search_query_id: int, active_item_ids: List[str]) -> int:
        """
        Синхронизация выдачи: помечает товары поиска, которых больше нет на Авито, как закрытые/проданные.
        """
        if not active_item_ids:
            return 0
        
        async with self.connection() as db:
            placeholders = ",".join(["?"] * len(active_item_ids))
            now = datetime.now()
            sql = f"""
                UPDATE items
                SET is_closed = 1, closed_at = COALESCE(closed_at, ?), updated_at = ?
                WHERE search_query_id = ?
                  AND id NOT IN ({placeholders})
                  AND (is_closed = 0 OR is_closed IS NULL)
            """
            params = [now, now, search_query_id] + list(active_item_ids)
            cursor = await db.execute(sql, params)
            await db.commit()
            return cursor.rowcount


    async def get_items(
        self,
        search_query_id: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
        min_price: Optional[int] = None,
        max_price: Optional[int] = None
    ) -> List[AvitoItem]:
        """Получение списка объявлений из базы данных с фильтрацией"""
        items, _ = await self.get_items_filtered(
            search_query_id=search_query_id,
            min_price=min_price,
            max_price=max_price,
            limit=limit,
            offset=offset
        )
        return items

    async def get_items_filtered(
        self,
        search_query_id: Optional[int] = None,
        query: Optional[str] = None,
        min_price: Optional[int] = None,
        max_price: Optional[int] = None,
        with_discount_only: bool = False,
        with_delivery_only: bool = False,
        favorites_only: bool = False,
        hot_deals_only: bool = False,
        gems_only: bool = False,
        hide_reserved: bool = False,
        deal_grade: Optional[str] = None,
        min_deal_score: Optional[int] = None,
        include_hidden: bool = False,
        sort_by: str = "newest",
        limit: int = 50,
        offset: int = 0
    ) -> Tuple[List[AvitoItem], int]:
        """
        Расширенное получение списка объявлений с пагинацией, подсчетом общего количества и сортировкой.
        """
        items: List[AvitoItem] = []
        where_clauses: List[str] = ["1=1"]
        params: List[Any] = []

        if not include_hidden:
            where_clauses.append("(is_hidden = 0 OR is_hidden IS NULL)")

        # Скрываем закрытые/снятые объявления
        where_clauses.append("(is_closed = 0 OR is_closed IS NULL)")

        if hide_reserved:
            where_clauses.append("(is_reserved = 0 OR is_reserved IS NULL)")

        if favorites_only:
            where_clauses.append("is_favorite = 1")

        if gems_only:
            where_clauses.append("deal_grade = 'GEM'")
        elif deal_grade:
            grade = deal_grade.upper()
            if grade == "HOT":
                where_clauses.append("deal_grade IN ('GEM', 'HOT')")
            elif grade == "FAIR":
                where_clauses.append("deal_grade IN ('GEM', 'HOT', 'FAIR')")
            else:
                where_clauses.append("deal_grade = ?")
                params.append(grade)
        elif hot_deals_only:
            where_clauses.append("(is_hot_deal = 1 OR deal_grade IN ('GEM', 'HOT'))")
        if min_deal_score is not None and min_deal_score > 0:
            where_clauses.append("deal_score >= ?")
            params.append(int(min_deal_score))

        if search_query_id is not None:
            if str(search_query_id) in ("-1", "unassigned", "null"):
                where_clauses.append("search_query_id IS NULL")
            else:
                try:
                    where_clauses.append("EXISTS (SELECT 1 FROM item_searches membership WHERE membership.item_id=items.id AND membership.search_id=?)")
                    params.append(int(search_query_id))
                except (ValueError, TypeError):
                    pass

        if query:
            where_clauses.append("(title LIKE ? OR description LIKE ? OR address LIKE ? OR params_json LIKE ?)")
            pattern = f"%{query.strip()}%"
            params.extend([pattern, pattern, pattern, pattern])

        if min_price is not None:
            where_clauses.append("price >= ?")
            params.append(min_price)

        if max_price is not None:
            where_clauses.append("price <= ?")
            params.append(max_price)

        if with_discount_only:
            where_clauses.append("(old_price IS NOT NULL AND price < old_price)")

        if with_delivery_only:
            where_clauses.append("delivery_available = 1")

        where_sql = " AND ".join(where_clauses)

        # Сортировка
        order_by_sql = "created_at DESC"
        if sort_by == "oldest":
            order_by_sql = "created_at ASC"
        elif sort_by == "price_asc":
            order_by_sql = "price ASC NULLS LAST"
        elif sort_by == "price_desc":
            order_by_sql = "price DESC NULLS LAST"
        elif sort_by == "discount_desc":
            order_by_sql = "(old_price - price) DESC NULLS LAST"
        elif sort_by in ("deal_score", "deal_score_desc", "score_desc", "score"):
            order_by_sql = "deal_score DESC, created_at DESC"
        elif sort_by == "title":
            order_by_sql = "title ASC"

        count_sql = f"SELECT COUNT(*) FROM items WHERE {where_sql}"
        select_sql = f"SELECT * FROM items WHERE {where_sql} ORDER BY {order_by_sql} LIMIT ? OFFSET ?"
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row

            # Подсчет общего количества
            cursor_cnt = await db.execute(count_sql, params)
            total_count_row = await cursor_cnt.fetchone()
            total_count = total_count_row[0] if total_count_row else 0

            # Выборка данных
            fetch_params = list(params)
            fetch_params.extend([limit, offset])
            cursor_sel = await db.execute(select_sql, fetch_params)
            rows = await cursor_sel.fetchall()

            for row in rows:
                items.append(self._row_to_item(row))

        return items, total_count

    async def get_item_by_id(self, item_id: str) -> Optional[AvitoItem]:
        """Поиск объявления по ID"""
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM items WHERE id = ?", (item_id,))
            row = await cursor.fetchone()
            if row:
                return self._row_to_item(row)
        return None

    async def delete_item(self, item_id: str) -> bool:
        """Удаление одного объявления"""
        async with self.connection() as db:
            await db.execute("DELETE FROM price_history WHERE item_id = ?", (item_id,))
            await db.execute("DELETE FROM sent_notifications WHERE item_id = ?", (item_id,))
            cursor = await db.execute("DELETE FROM items WHERE id = ?", (item_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def delete_items_batch(self, item_ids: List[str]) -> int:
        """Пакетное удаление объявлений"""
        if not item_ids:
            return 0
        async with self.connection() as db:
            placeholders = ",".join("?" for _ in item_ids)
            await db.execute(f"DELETE FROM price_history WHERE item_id IN ({placeholders})", item_ids)
            await db.execute(f"DELETE FROM sent_notifications WHERE item_id IN ({placeholders})", item_ids)
            cursor = await db.execute(f"DELETE FROM items WHERE id IN ({placeholders})", item_ids)
            await db.commit()
            return cursor.rowcount

    async def clear_items(self) -> int:
        """Полная очистка всех собранных объявлений"""
        async with self.connection() as db:
            await db.execute("DELETE FROM price_history")
            await db.execute("DELETE FROM sent_notifications")
            cursor = await db.execute("DELETE FROM items")
            await db.commit()
            return cursor.rowcount

    # Методы работы с поисковыми запросами (мониторингом)
    async def add_search(self, search: SearchQuery) -> int:
        """Добавление нового поискового запроса для мониторинга"""
        async with self.connection() as db:
            cursor = await db.execute("""
                INSERT INTO searches (name, url, min_price, max_price, check_interval_min, enabled, active_hours_start, active_hours_end, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                search.name, search.url, search.min_price, search.max_price,
                search.check_interval_min, search.enabled,
                search.active_hours_start, search.active_hours_end, datetime.now()
            ))
            await db.commit()
            return cursor.lastrowid or 0

    async def update_search(self, search: SearchQuery) -> bool:
        """Обновление параметров поискового запроса"""
        if not search.id:
            return False
        async with self.connection() as db:
            cursor = await db.execute("""
                UPDATE searches SET
                    name = ?,
                    url = ?,
                    min_price = ?,
                    max_price = ?,
                    check_interval_min = ?,
                    enabled = ?,
                    active_hours_start = ?,
                    active_hours_end = ?
                WHERE id = ?
            """, (
                search.name, search.url, search.min_price, search.max_price,
                search.check_interval_min, search.enabled,
                search.active_hours_start, search.active_hours_end, search.id
            ))
            await db.commit()
            return cursor.rowcount > 0

    async def toggle_search_enabled(self, search_id: int, enabled: Optional[bool] = None) -> bool:
        """Включение/выключение задачи мониторинга"""
        async with self.connection() as db:
            if enabled is None:
                cursor = await db.execute("UPDATE searches SET enabled = CASE WHEN enabled = 1 THEN 0 ELSE 1 END WHERE id = ?", (search_id,))
            else:
                cursor = await db.execute("UPDATE searches SET enabled = ? WHERE id = ?", (1 if enabled else 0, search_id))
            await db.commit()
            return cursor.rowcount > 0

    async def get_searches(self, enabled_only: bool = False) -> List[SearchQuery]:
        """Получение списка запросов для мониторинга"""
        searches: List[SearchQuery] = []
        query = "SELECT * FROM searches"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY id ASC"

        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query)
            rows = await cursor.fetchall()
            for row in rows:
                cols_s = row.keys() if hasattr(row, "keys") else []
                searches.append(SearchQuery(
                    id=row["id"],
                    name=row["name"],
                    url=row["url"],
                    min_price=row["min_price"],
                    max_price=row["max_price"],
                    check_interval_min=row["check_interval_min"],
                    enabled=bool(row["enabled"]),
                    active_hours_start=row["active_hours_start"] if "active_hours_start" in cols_s else 0,
                    active_hours_end=row["active_hours_end"] if "active_hours_end" in cols_s else 24,
                    last_checked_at=datetime.fromisoformat(row["last_checked_at"]) if row["last_checked_at"] else None,
                    created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.now()
                ))
        return searches

    async def get_searches_with_counts(self) -> List[Dict[str, Any]]:
        """Получение списка запросов с количеством собранных товаров"""
        result: List[Dict[str, Any]] = []
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT s.*, COUNT(i.id) AS items_count
                FROM searches s
                LEFT JOIN item_searches membership ON s.id=membership.search_id
                LEFT JOIN items i ON i.id=membership.item_id
                GROUP BY s.id
                ORDER BY s.id ASC
            """)
            rows = await cursor.fetchall()
            for r in rows:
                cols_s = r.keys() if hasattr(r, "keys") else []
                result.append({
                    "id": r["id"],
                    "name": r["name"],
                    "url": r["url"],
                    "min_price": r["min_price"],
                    "max_price": r["max_price"],
                    "check_interval_min": r["check_interval_min"],
                    "enabled": bool(r["enabled"]),
                    "active_hours_start": r["active_hours_start"] if "active_hours_start" in cols_s else 0,
                    "active_hours_end": r["active_hours_end"] if "active_hours_end" in cols_s else 24,
                    "last_checked_at": r["last_checked_at"],
                    "created_at": r["created_at"],
                    "items_count": r["items_count"]
                })
        return result

    async def get_search_by_id(self, search_id: int) -> Optional[SearchQuery]:
        """Получение запроса по ID"""
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM searches WHERE id = ?", (search_id,))
            row = await cursor.fetchone()
            if row:
                cols_s = row.keys() if hasattr(row, "keys") else []
                return SearchQuery(
                    id=row["id"],
                    name=row["name"],
                    url=row["url"],
                    min_price=row["min_price"],
                    max_price=row["max_price"],
                    check_interval_min=row["check_interval_min"],
                    enabled=bool(row["enabled"]),
                    active_hours_start=row["active_hours_start"] if "active_hours_start" in cols_s else 0,
                    active_hours_end=row["active_hours_end"] if "active_hours_end" in cols_s else 24,
                    last_checked_at=datetime.fromisoformat(row["last_checked_at"]) if row["last_checked_at"] else None,
                    created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.now()
                )

    async def delete_search(self, search_id: int) -> bool:
        """Удаление поискового запроса"""
        async with self.connection() as db:
            cursor = await db.execute("DELETE FROM searches WHERE id = ?", (search_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def update_search_last_checked(self, search_id: int) -> None:
        """Обновление метки последней проверки"""
        async with self.connection() as db:
            await db.execute("UPDATE searches SET last_checked_at = ? WHERE id = ?", (datetime.now(), search_id))
            await db.commit()

    # Методы работы с историей цен и падениями цен
    async def get_price_history(self, item_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """История изменений цен"""
        history: List[Dict[str, Any]] = []
        query = "SELECT * FROM price_history"
        params: List[Any] = []
        if item_id:
            query += " WHERE item_id = ?"
            params.append(item_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            for r in rows:
                history.append({
                    "id": r["id"],
                    "item_id": r["item_id"],
                    "old_price": r["old_price"],
                    "new_price": r["new_price"],
                    "delta": r["delta"],
                    "created_at": r["created_at"]
                })
        return history

    async def get_price_drops(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Получение списка всех зафиксированных скидок с деталями товаров"""
        drops: List[Dict[str, Any]] = []
        query = """
            SELECT ph.id AS history_id, ph.item_id, ph.old_price, ph.new_price, ph.delta, ph.created_at AS dropped_at,
                   i.title, i.main_image, i.url, i.address, i.metro
            FROM price_history ph
            JOIN items i ON ph.item_id = i.id
            WHERE ph.delta < 0
              AND (i.is_hidden = 0 OR i.is_hidden IS NULL)
              AND (i.is_closed = 0 OR i.is_closed IS NULL)
              AND (i.is_reserved = 0 OR i.is_reserved IS NULL)
            ORDER BY ph.created_at DESC
            LIMIT ?
        """
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, (limit,))
            rows = await cursor.fetchall()
            for r in rows:
                discount_pct = round((abs(r["delta"]) / r["old_price"]) * 100, 1) if r["old_price"] > 0 else 0
                drops.append({
                    "history_id": r["history_id"],
                    "item_id": r["item_id"],
                    "title": r["title"],
                    "main_image": r["main_image"],
                    "url": r["url"],
                    "address": r["address"],
                    "metro": r["metro"],
                    "old_price": r["old_price"],
                    "new_price": r["new_price"],
                    "delta": r["delta"],
                    "discount_pct": discount_pct,
                    "dropped_at": r["dropped_at"]
                })
        return drops

    # Методы работы с историей уведомлений
    async def is_notification_sent(self, item_id: str, search_query_id: Optional[int], notif_type: str) -> bool:
        """Проверка, было ли уже отправлено уведомление данного типа"""
        async with self.connection() as db:
            cursor = await db.execute("""
                SELECT 1 FROM sent_notifications
                WHERE item_id = ? AND search_query_id IS ? AND notification_type = ?
            """, (item_id, search_query_id, notif_type))
            return await cursor.fetchone() is not None

    async def mark_notification_sent(self, item_id: str, search_query_id: Optional[int], notif_type: str) -> None:
        """Фиксация отправленного уведомления в БД"""
        async with self.connection() as db:
            await db.execute("""
                INSERT OR IGNORE INTO sent_notifications (item_id, search_query_id, notification_type, sent_at)
                VALUES (?, ?, ?, ?)
            """, (item_id, search_query_id, notif_type, datetime.now()))
            await db.commit()

    async def get_stats(self) -> Dict[str, Any]:
        """Статистика базы данных"""
        async with self.connection() as db:
            items_res = await (await db.execute("SELECT COUNT(*) FROM items WHERE (is_hidden = 0 OR is_hidden IS NULL) AND (is_closed = 0 OR is_closed IS NULL) AND (is_reserved = 0 OR is_reserved IS NULL)")).fetchone()
            searches_res = await (await db.execute("SELECT COUNT(*) FROM searches")).fetchone()
            active_searches_res = await (await db.execute("SELECT COUNT(*) FROM searches WHERE enabled = 1")).fetchone()
            price_drops_res = await (await db.execute("SELECT COUNT(*) FROM price_history ph JOIN items i ON ph.item_id = i.id WHERE ph.delta < 0 AND (i.is_hidden = 0 OR i.is_hidden IS NULL) AND (i.is_closed = 0 OR i.is_closed IS NULL) AND (i.is_reserved = 0 OR i.is_reserved IS NULL)")).fetchone()

            # Новых товаров сегодня
            today_items = await (await db.execute("SELECT COUNT(*) FROM items WHERE DATE(created_at) = DATE('now') AND (is_hidden = 0 OR is_hidden IS NULL) AND (is_closed = 0 OR is_closed IS NULL) AND (is_reserved = 0 OR is_reserved IS NULL)")).fetchone()
            # Скидок сегодня
            today_drops = await (await db.execute("SELECT COUNT(*) FROM price_history ph JOIN items i ON ph.item_id = i.id WHERE ph.delta < 0 AND DATE(ph.created_at) = DATE('now') AND (i.is_hidden = 0 OR i.is_hidden IS NULL) AND (i.is_closed = 0 OR i.is_closed IS NULL) AND (i.is_reserved = 0 OR i.is_reserved IS NULL)")).fetchone()

            # Статистика выгодных лотов (только доступные)
            gems_res = await (await db.execute("SELECT COUNT(*) FROM items WHERE deal_grade = 'GEM' AND (is_hidden = 0 OR is_hidden IS NULL) AND (is_closed = 0 OR is_closed IS NULL) AND (is_reserved = 0 OR is_reserved IS NULL)")).fetchone()
            hot_res = await (await db.execute("SELECT COUNT(*) FROM items WHERE deal_grade IN ('GEM', 'HOT') AND (is_hidden = 0 OR is_hidden IS NULL) AND (is_closed = 0 OR is_closed IS NULL) AND (is_reserved = 0 OR is_reserved IS NULL)")).fetchone()

            return {
                "total_items": items_res[0] if items_res else 0,
                "total_searches": searches_res[0] if searches_res else 0,
                "active_searches": active_searches_res[0] if active_searches_res else 0,
                "total_price_drops": price_drops_res[0] if price_drops_res else 0,
                "items_today": today_items[0] if today_items else 0,
                "price_drops_today": today_drops[0] if today_drops else 0,
                "total_gems": gems_res[0] if gems_res else 0,
                "total_hot_deals": hot_res[0] if hot_res else 0,
            }

    async def get_dashboard_summary(self) -> Dict[str, Any]:
        """Полный набор данных для аналитического дашборда"""
        stats = await self.get_stats()
        searches = await self.get_searches_with_counts()
        recent_drops = await self.get_price_drops(limit=6)
        recent_items, _ = await self.get_items_filtered(limit=6)
        top_deals = await self.get_top_deal_items(limit=6)

        # Распределение по ценовым диапазонам для графика
        # Распределение по ценовым диапазонам для графика (только доступные товары)
        active_filter = "(is_hidden = 0 OR is_hidden IS NULL) AND (is_closed = 0 OR is_closed IS NULL) AND (is_reserved = 0 OR is_reserved IS NULL)"
        async with self.connection() as db:
            tier1 = await (await db.execute(f"SELECT COUNT(*) FROM items WHERE price < 10000 AND {active_filter}")).fetchone()
            tier2 = await (await db.execute(f"SELECT COUNT(*) FROM items WHERE price >= 10000 AND price < 50000 AND {active_filter}")).fetchone()
            tier3 = await (await db.execute(f"SELECT COUNT(*) FROM items WHERE price >= 50000 AND price < 100000 AND {active_filter}")).fetchone()
            tier4 = await (await db.execute(f"SELECT COUNT(*) FROM items WHERE price >= 100000 AND {active_filter}")).fetchone()
        price_distribution = {
            "under_10k": tier1[0] if tier1 else 0,
            "10k_50k": tier2[0] if tier2 else 0,
            "50k_100k": tier3[0] if tier3 else 0,
            "over_100k": tier4[0] if tier4 else 0,
        }

        return {
            "stats": stats,
            "searches": searches,
            "recent_drops": recent_drops,
            "recent_items": [it.model_dump(mode="json") for it in recent_items],
            "top_deals": [it.model_dump(mode="json") for it in top_deals],
            "price_distribution": price_distribution
        }
    def _row_to_item(self, row: Any) -> AvitoItem:
        """Преобразование строки SQLite в модель AvitoItem"""
        images = []
        if row["images_json"]:
            try:
                images = json.loads(row["images_json"])
            except Exception:
                images = []

        params = {}
        if row["params_json"]:
            try:
                params = json.loads(row["params_json"])
            except Exception:
                params = {}

        seller = None
        if row["seller_json"]:
            try:
                s_dict = json.loads(row["seller_json"])
                if s_dict:
                    seller = SellerInfo(**s_dict)
            except Exception:
                seller = None

        cols = row.keys() if hasattr(row, "keys") else []

        deal_reasons = []
        if "deal_reasons_json" in cols and row["deal_reasons_json"]:
            try:
                deal_reasons = json.loads(row["deal_reasons_json"])
            except Exception:
                deal_reasons = []

        detected_flaws = []
        if "detected_flaws_json" in cols and row["detected_flaws_json"]:
            try:
                detected_flaws = json.loads(row["detected_flaws_json"])
            except Exception:
                detected_flaws = []

        deal_score = int(row["deal_score"]) if "deal_score" in cols and row["deal_score"] is not None else 50
        deal_grade = str(row["deal_grade"]) if "deal_grade" in cols and row["deal_grade"] else "FAIR"
        ai_summary = row["ai_summary"] if "ai_summary" in cols else None

        return AvitoItem(
            id=str(row["id"]),
            title=row["title"],
            price=row["price"],
            old_price=row["old_price"],
            price_string=row["price_string"],
            url=row["url"],
            address=row["address"],
            metro=row["metro"],
            published_at=row["published_at"],
            description=row["description"],
            main_image=row["main_image"],
            images=images,
            params=params,
            seller=seller,
            delivery_available=bool(row["delivery_available"]),
            is_vip=bool(row["is_vip"]),
            category=row["category"],
            search_query_id=row["search_query_id"],
            is_favorite=bool(row["is_favorite"]) if "is_favorite" in cols else False,
            is_hidden=bool(row["is_hidden"]) if "is_hidden" in cols else False,
            is_reserved=bool(row["is_reserved"]) if "is_reserved" in cols else False,
            is_closed=bool(row["is_closed"]) if "is_closed" in cols else False,
            market_avg_price=row["market_avg_price"] if "market_avg_price" in cols else None,
            is_hot_deal=bool(row["is_hot_deal"]) if "is_hot_deal" in cols else False,
            deal_score=deal_score,
            score_version=row['score_version'] if 'score_version' in cols else 'legacy-1',
            score_confidence=row['score_confidence'] if 'score_confidence' in cols else 0,
            deal_grade=deal_grade,
            deal_reasons=deal_reasons,
            ai_summary=ai_summary,
            detected_flaws=detected_flaws,
            content_hash=row["content_hash"] if "content_hash" in cols else None,
            closed_at=datetime.fromisoformat(str(row["closed_at"])) if ("closed_at" in cols and row["closed_at"]) else None,
            created_at=datetime.fromisoformat(str(row["created_at"])) if row["created_at"] else datetime.now(),
            updated_at=datetime.fromisoformat(str(row["updated_at"])) if row["updated_at"] else datetime.now(),
        )
    # АВТОРИЗАЦИЯ И ПОЛЬЗОВАТЕЛИ (PBKDF2-SHA256)
    # ==========================================================================

    @staticmethod
    def hash_password(password: str, salt_hex: Optional[str] = None) -> Tuple[str, str]:
        """Криптографическое хеширование пароля через PBKDF2-HMAC-SHA256 (100k итераций)"""
        salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
        key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
        return key.hex(), salt.hex()

    @staticmethod
    def verify_password(password: str, hash_hex: str, salt_hex: str) -> bool:
        """Сверка пароля с хешем в базе"""
        salt = bytes.fromhex(salt_hex)
        check_key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
        return secrets.compare_digest(check_key.hex(), hash_hex)

    async def init_default_admin(self) -> None:
        """Bootstrap only from an explicitly configured secret."""
        password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
        if not password:
            return
        async with self.connection() as db:
            cursor = await db.execute("SELECT COUNT(*) FROM users")
            if (await cursor.fetchone())[0]:
                return
        await self.bootstrap_admin(os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin"), password)

    async def bootstrap_admin(self, username: str, password: str) -> None:
        if len(username.strip()) < 3 or len(password) < 12:
            raise ValueError("Имя должно содержать 3 символа, пароль — не менее 12")
        async with self.connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute("SELECT COUNT(*) FROM users")
            count_row = await cursor.fetchone()
            if count_row and count_row[0] == 0:
                pwd_hash, salt = self.hash_password(password)
                await db.execute("""
                    INSERT INTO users (username, password_hash, salt, role, is_active, created_at)
                    VALUES (?, ?, ?, ?, 1, ?)
                """, (username.strip(), pwd_hash, salt, UserRole.ADMIN.value, datetime.now()))
                await db.commit()
            else:
                raise ValueError("Первый администратор уже настроен")

    async def authenticate_user(self, username: str, password: str) -> Optional[User]:
        """Проверка логина и пароля пользователя"""
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM users WHERE username = ? AND is_active = 1", (username.strip(),))
            row = await cursor.fetchone()
            if not row:
                return None

            if not self.verify_password(password, row["password_hash"], row["salt"]):
                return None

            now = datetime.now()
            await db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, row["id"]))
            await db.commit()

            return User(
                id=row["id"],
                username=row["username"],
                role=UserRole(row["role"]),
                is_active=bool(row["is_active"]),
                created_at=datetime.fromisoformat(str(row["created_at"])) if row["created_at"] else None,
                last_login_at=now
            )

    async def create_user(self, username: str, password: str, role: UserRole = UserRole.OPERATOR) -> Tuple[bool, str, Optional[int]]:
        """Создание нового пользователя"""
        username = username.strip()
        if len(username) < 3:
            return False, "Имя пользователя должно содержать не менее 3 символов", None
        if len(password) < 12:
            return False, "Пароль должен содержать не менее 12 символов", None

        async with self.connection() as db:
            cursor = await db.execute("SELECT 1 FROM users WHERE username = ?", (username,))
            if await cursor.fetchone():
                return False, f"Пользователь с именем '{username}' уже существует", None

            pwd_hash, salt = self.hash_password(password)
            cur_ins = await db.execute("""
                INSERT INTO users (username, password_hash, salt, role, is_active, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
            """, (username, pwd_hash, salt, role.value, datetime.now()))
            await db.commit()
            return True, "Пользователь успешно создан", cur_ins.lastrowid

    async def get_all_users(self) -> List[User]:
        """Получение списка всех пользователей"""
        users: List[User] = []
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT id, username, role, is_active, created_at, last_login_at FROM users ORDER BY id ASC")
            rows = await cursor.fetchall()
            for r in rows:
                users.append(User(
                    id=r["id"],
                    username=r["username"],
                    role=UserRole(r["role"]),
                    is_active=bool(r["is_active"]),
                    created_at=datetime.fromisoformat(str(r["created_at"])) if r["created_at"] else None,
                    last_login_at=datetime.fromisoformat(str(r["last_login_at"])) if r["last_login_at"] else None
                ))
        return users

    async def get_user_by_id(self, user_id: int) -> Optional[User]:
        """Поиск пользователя по ID"""
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT id, username, role, is_active, created_at, last_login_at FROM users WHERE id = ?", (user_id,))
            r = await cursor.fetchone()
            if r:
                return User(
                    id=r["id"],
                    username=r["username"],
                    role=UserRole(r["role"]),
                    is_active=bool(r["is_active"]),
                    created_at=datetime.fromisoformat(str(r["created_at"])) if r["created_at"] else None,
                    last_login_at=datetime.fromisoformat(str(r["last_login_at"])) if r["last_login_at"] else None
                )
        return None

    async def update_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        role: Optional[UserRole] = None,
        is_active: Optional[bool] = None,
        new_password: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Обновление данных пользователя"""
        async with self.connection() as db:
            # Проверка существования
            cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            user_row = await cursor.fetchone()
            if not user_row:
                return False, "Пользователь не найден"

            updates = []
            params = []

            if username is not None and username.strip():
                u = username.strip()
                # Проверка уникальности
                c_chk = await db.execute("SELECT 1 FROM users WHERE username = ? AND id != ?", (u, user_id))
                if await c_chk.fetchone():
                    return False, f"Имя '{u}' уже занято другим пользователем"
                updates.append("username = ?")
                params.append(u)

            if role is not None:
                # Защита от снятия прав с последнего активного админа
                if user_row[4] == UserRole.ADMIN.value and role != UserRole.ADMIN:
                    c_adm = await db.execute("SELECT COUNT(*) FROM users WHERE role = ? AND is_active = 1", (UserRole.ADMIN.value,))
                    cnt = (await c_adm.fetchone())[0]
                    if cnt <= 1:
                        return False, "Нельзя изменить роль последнего активного администратора"
                updates.append("role = ?")
                params.append(role.value)

            if is_active is not None:
                if user_row[4] == UserRole.ADMIN.value and not is_active:
                    c_adm = await db.execute("SELECT COUNT(*) FROM users WHERE role = ? AND is_active = 1", (UserRole.ADMIN.value,))
                    cnt = (await c_adm.fetchone())[0]
                    if cnt <= 1:
                        return False, "Нельзя деактивировать последнего активного администратора"
                updates.append("is_active = ?")
                params.append(1 if is_active else 0)

            if new_password is not None and new_password.strip():
                if len(new_password.strip()) < 12:
                    return False, "Новый пароль должен содержать минимум 12 символов"
                pwd_hash, salt = self.hash_password(new_password.strip())
                updates.append("password_hash = ?")
                params.append(pwd_hash)
                updates.append("salt = ?")
                params.append(salt)

            if not updates:
                return True, "Нет изменений"

            params.append(user_id)
            sql = f"UPDATE users SET {', '.join(updates)} WHERE id = ?"
            await db.execute(sql, params)
            if new_password is not None or is_active is False or role is not None:
                await db.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
            await db.commit()
            return True, "Данные пользователя успешно обновлены"

    async def delete_user(self, user_id: int) -> Tuple[bool, str]:
        """Удаление пользователя с защитой последнего админа"""
        async with self.connection() as db:
            cursor = await db.execute("SELECT role FROM users WHERE id = ?", (user_id,))
            row = await cursor.fetchone()
            if not row:
                return False, "Пользователь не найден"

            if row[0] == UserRole.ADMIN.value:
                c_adm = await db.execute("SELECT COUNT(*) FROM users WHERE role = ? AND is_active = 1", (UserRole.ADMIN.value,))
                cnt = (await c_adm.fetchone())[0]
                if cnt <= 1:
                    return False, "Нельзя удалить единственного администратора системы"

            await db.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
            await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
            await db.commit()
            return True, "Пользователь удален"

    async def change_password(self, user_id: int, old_password: str, new_password: str) -> Tuple[bool, str]:
        """Смена собственного пароля пользователем"""
        if len(new_password) < 12:
            return False, "Новый пароль должен быть не менее 12 символов"

        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            user_row = await cursor.fetchone()
            if not user_row:
                return False, "Пользователь не найден"

            if not self.verify_password(old_password, user_row["password_hash"], user_row["salt"]):
                return False, "Текущий пароль указан неверно"

            pwd_hash, salt = self.hash_password(new_password)
            await db.execute("UPDATE users SET password_hash = ?, salt = ? WHERE id = ?", (pwd_hash, salt, user_id))
            await db.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
            await db.commit()
            return True, "Пароль успешно изменен"

    # ==========================================================================
    # СЕССИИ ПОЛЬЗОВАТЕЛЕЙ
    # ==========================================================================

    async def create_session(self, user_id: int, days_valid: int = 14) -> str:
        """Создание защищенного токена сессии"""
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(days=days_valid)
        async with self.connection() as db:
            await db.execute("""
                INSERT INTO user_sessions (token, user_id, expires_at, created_at)
                VALUES (?, ?, ?, ?)
            """, (token, user_id, expires_at, datetime.now()))
            await db.commit()
        return token

    async def get_user_by_session(self, token: str) -> Optional[User]:
        """Получение пользователя по токену сессии"""
        if not token:
            return None
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT u.id, u.username, u.role, u.is_active, u.created_at, u.last_login_at
                FROM user_sessions s
                JOIN users u ON s.user_id = u.id
                WHERE s.token = ? AND s.expires_at > ? AND u.is_active = 1
            """, (token, datetime.now()))
            r = await cursor.fetchone()
            if r:
                return User(
                    id=r["id"],
                    username=r["username"],
                    role=UserRole(r["role"]),
                    is_active=bool(r["is_active"]),
                    created_at=datetime.fromisoformat(str(r["created_at"])) if r["created_at"] else None,
                    last_login_at=datetime.fromisoformat(str(r["last_login_at"])) if r["last_login_at"] else None
                )
        return None

    async def delete_session(self, token: str) -> None:
        """Удаление сессии при выходе"""
        if not token:
            return
        async with self.connection() as db:
            await db.execute("DELETE FROM user_sessions WHERE token = ?", (token,))
            await db.commit()

    # ==========================================================================
    # НАСТРОЙКИ УВЕДОМЛЕНИЙ TELEGRAM ДЛЯ ЧАТОВ
    # ==========================================================================

    async def get_chat_settings(self, chat_id: int) -> TelegramChatSettings:
        """Получение персональных настроек уведомлений для чата Telegram"""
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM telegram_chat_settings WHERE chat_id = ?", (chat_id,))
            r = await cursor.fetchone()
            if r:
                cols = r.keys() if hasattr(r, "keys") else []
                return TelegramChatSettings(
                    chat_id=r["chat_id"],
                    notify_new=bool(r["notify_new"]),
                    notify_drops=bool(r["notify_drops"]),
                    send_photos=bool(r["send_photos"]),
                    delivery_only=bool(r["delivery_only"]),
                    min_discount_pct=int(r["min_discount_pct"] or 0),
                    min_deal_score=int(r["min_deal_score"] or 0) if "min_deal_score" in cols else 0,
                    only_below_market=bool(r["only_below_market"]) if "only_below_market" in cols else False,
                    below_market_pct=int(r["below_market_pct"] or 15) if "below_market_pct" in cols else 15,
                    updated_at=datetime.fromisoformat(str(r["updated_at"])) if r["updated_at"] else None
                )
            # Дефолтные настройки
            return TelegramChatSettings(
                chat_id=chat_id,
                notify_new=True,
                notify_drops=True,
                send_photos=config.telegram.send_photos,
                delivery_only=False,
                min_discount_pct=0,
                min_deal_score=0,
                only_below_market=False,
                below_market_pct=15
            )

    async def update_chat_settings(
        self,
        chat_id: int,
        notify_new: Optional[bool] = None,
        notify_drops: Optional[bool] = None,
        send_photos: Optional[bool] = None,
        delivery_only: Optional[bool] = None,
        min_discount_pct: Optional[int] = None,
        min_deal_score: Optional[int] = None,
        only_below_market: Optional[bool] = None,
        below_market_pct: Optional[int] = None
    ) -> TelegramChatSettings:
        """Обновление настроек уведомлений конкретного чата Telegram"""
        current = await self.get_chat_settings(chat_id)

        new_notify_new = notify_new if notify_new is not None else current.notify_new
        new_notify_drops = notify_drops if notify_drops is not None else current.notify_drops
        new_send_photos = send_photos if send_photos is not None else current.send_photos
        new_delivery_only = delivery_only if delivery_only is not None else current.delivery_only
        new_min_discount = min_discount_pct if min_discount_pct is not None else current.min_discount_pct
        new_min_deal_score = min_deal_score if min_deal_score is not None else current.min_deal_score
        new_only_below = only_below_market if only_below_market is not None else current.only_below_market
        new_below_pct = below_market_pct if below_market_pct is not None else current.below_market_pct

        async with self.connection() as db:
            await db.execute("""
                INSERT INTO telegram_chat_settings (
                    chat_id, notify_new, notify_drops, send_photos, delivery_only,
                    min_discount_pct, min_deal_score, only_below_market, below_market_pct, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    notify_new = excluded.notify_new,
                    notify_drops = excluded.notify_drops,
                    send_photos = excluded.send_photos,
                    delivery_only = excluded.delivery_only,
                    min_discount_pct = excluded.min_discount_pct,
                    min_deal_score = excluded.min_deal_score,
                    only_below_market = excluded.only_below_market,
                    below_market_pct = excluded.below_market_pct,
                    updated_at = excluded.updated_at
            """, (
                chat_id, 1 if new_notify_new else 0, 1 if new_notify_drops else 0,
                1 if new_send_photos else 0, 1 if new_delivery_only else 0,
                new_min_discount, new_min_deal_score, 1 if new_only_below else 0, new_below_pct, datetime.now()
            ))
            await db.commit()

        return await self.get_chat_settings(chat_id)

    async def get_search_market_stats(self, search_query_id: Optional[int] = None) -> Tuple[Optional[int], Optional[int]]:
        """Расчет медианной и средней рыночной стоимости товаров в конкретном поиске"""
        if search_query_id is None:
            return None, None

        where = "EXISTS (SELECT 1 FROM item_searches membership WHERE membership.item_id=items.id AND membership.search_id=?) AND price IS NOT NULL AND price > 0 AND COALESCE(is_hidden,0)=0 AND COALESCE(is_closed,0)=0"
        params = [search_query_id]
        
        async with self.connection() as db:
            cursor = await db.execute(f"SELECT COUNT(*), AVG(price) FROM items WHERE {where}", params)
            row = await cursor.fetchone()
            if not row or not row[0]:
                return None, None
            count, avg_p = row[0], int(row[1]) if row[1] else None
            
            cursor_med = await db.execute(f"SELECT price FROM items WHERE {where} ORDER BY price ASC LIMIT ? OFFSET ?", params + [2 if count % 2 == 0 else 1, (count - 1) // 2])
            middle = await cursor_med.fetchall()
            med_p = int(sum(row[0] for row in middle) / len(middle)) if middle else avg_p
            return med_p, avg_p

    async def get_ai_settings(self) -> AISettings:
        """Получение сохраненных настроек AI-провайдера"""
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM ai_settings WHERE id = 1")
            r = await cursor.fetchone()
            if r:
                return AISettings(
                    enabled=bool(r["enabled"]),
                    provider=r["provider"] or "deepseek",
                    api_key=r["api_key"] or "",
                    model=r["model"] or "deepseek-chat",
                    api_base=r["api_base"] or "https://api.deepseek.com",
                    prompt_template=r["prompt_template"] if "prompt_template" in r.keys() else None,
                    updated_at=datetime.fromisoformat(str(r["updated_at"])) if r["updated_at"] else None
                )
            return AISettings(
                enabled=False,
                provider="deepseek",
                api_key="",
                model="deepseek-chat",
                api_base="https://api.deepseek.com"
            )

    async def save_ai_settings(self, settings: AISettings) -> AISettings:
        """Сохранение настроек AI-провайдера"""
        now = datetime.now()
        async with self.connection() as db:
            await db.execute("""
                INSERT INTO ai_settings (id, enabled, provider, api_key, model, api_base, prompt_template, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    enabled = excluded.enabled,
                    provider = excluded.provider,
                    api_key = excluded.api_key,
                    model = excluded.model,
                    api_base = excluded.api_base,
                    prompt_template = excluded.prompt_template,
                    updated_at = excluded.updated_at
            """, (
                1 if settings.enabled else 0,
                settings.provider,
                settings.api_key,
                settings.model,
                settings.api_base,
                settings.prompt_template,
                now
            ))
            await db.commit()
        return await self.get_ai_settings()

    async def update_item_ai_summary(self, item_id: str, summary: str) -> bool:
        """Сохранение сгенерированного AI-резюме для товара"""
        async with self.connection() as db:
            cursor = await db.execute("UPDATE items SET ai_summary = ?, updated_at = ? WHERE id = ?", (summary, datetime.now(), item_id))
            await db.commit()
            return cursor.rowcount > 0

    async def recalculate_all_deal_scores(self) -> int:
        """Фоновый пересчет скоринга и градации для всех товаров в базе"""
        items_to_update: List[AvitoItem] = []
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM items")
            rows = await cursor.fetchall()
            for r in rows:
                items_to_update.append(self._row_to_item(r))

        if not items_to_update:
            return 0

        # Кэш рыночных цен по поискам
        stats_cache: Dict[Optional[int], Tuple[Optional[int], Optional[int]]] = {}
        async with self.connection() as db:
            for it in items_to_update:
                sq_id = it.search_query_id
                if sq_id not in stats_cache:
                    stats_cache[sq_id] = await self.get_search_market_stats(sq_id)
                
                med_p, avg_p = stats_cache[sq_id]
                score, grade, reasons, flaws = deal_scoring_engine.evaluate_item(
                    it, market_median=med_p, market_avg=avg_p
                )
                it.deal_score = score
                it.deal_grade = grade
                it.deal_reasons = reasons
                it.detected_flaws = flaws
                it.is_hot_deal = (grade in ("GEM", "HOT"))

                reasons_json = json.dumps(reasons, ensure_ascii=False)
                flaws_json = json.dumps(flaws, ensure_ascii=False)

                await db.execute("""
                    UPDATE items SET
                        deal_score = ?,
                        deal_grade = ?,
                        deal_reasons_json = ?,
                        detected_flaws_json = ?,
                        is_hot_deal = ?
                    WHERE id = ?
                """, (
                    score, grade, reasons_json, flaws_json,
                    1 if it.is_hot_deal else 0, it.id
                ))
            await db.commit()

        return len(items_to_update)

    async def get_top_deal_items(self, limit: int = 6) -> List[AvitoItem]:
        """Получение топ-товаров по AI Score (только доступные Gems & Hot deals без брони)"""
        items: List[AvitoItem] = []
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT * FROM items
                WHERE (is_hidden = 0 OR is_hidden IS NULL)
                  AND (is_closed = 0 OR is_closed IS NULL)
                  AND (is_reserved = 0 OR is_reserved IS NULL)
                  AND deal_score >= 70
                ORDER BY deal_score DESC, created_at DESC
                LIMIT ?
            """, (limit,))
            rows = await cursor.fetchall()
            for r in rows:
                items.append(self._row_to_item(r))
        return items

    async def get_search_market_price(self, search_query_id: Optional[int]) -> Optional[int]:
        """Расчет средней рыночной стоимости товаров в конкретном поиске"""
        _, avg_p = await self.get_search_market_stats(search_query_id)
        return avg_p

    # ==========================================================================
    # АНАЛИТИКА ЦЕН, ИЗБРАННОЕ И МАССОВЫЕ ДЕЙСТВИЯ
    # ==========================================================================

    async def get_price_stats(
        self,
        search_query_id: Optional[int] = None,
        query: Optional[str] = None
    ) -> PriceStats:
        """Расчет детальной ценовой статистики (мин, макс, средняя, медиана, оценка пула)"""
        where_clauses = [
            "price IS NOT NULL AND price > 0",
            "(is_hidden = 0 OR is_hidden IS NULL)",
            "(is_closed = 0 OR is_closed IS NULL)",
            "(is_reserved = 0 OR is_reserved IS NULL)"
        ]
        params: List[Any] = []
        if search_query_id is not None:
            if str(search_query_id) in ("-1", "unassigned", "null"):
                where_clauses.append("search_query_id IS NULL")
            else:
                where_clauses.append("EXISTS (SELECT 1 FROM item_searches membership WHERE membership.item_id=items.id AND membership.search_id=?)")
                params.append(int(search_query_id))

        if query:
            where_clauses.append("(title LIKE ? OR description LIKE ?)")
            pattern = f"%{query.strip()}%"
            params.extend([pattern, pattern])

        where_sql = " AND ".join(where_clauses)

        async with self.connection() as db:
            cursor = await db.execute(f"""
                SELECT COUNT(*), MIN(price), MAX(price), AVG(price), SUM(price)
                FROM items WHERE {where_sql}
            """, params)
            agg = await cursor.fetchone()

            if not agg or agg[0] == 0:
                return PriceStats()

            count, min_p, max_p, avg_p, sum_p = agg

            # Расчет медианной цены
            cursor_med = await db.execute(f"""
                SELECT price FROM items WHERE {where_sql} ORDER BY price ASC LIMIT ? OFFSET ?
            """, params + [2 if count % 2 == 0 else 1, (count - 1) // 2])
            middle = await cursor_med.fetchall()
            median_p = int(sum(row[0] for row in middle) / len(middle)) if middle else int(avg_p)

            return PriceStats(
                item_count=count,
                min_price=min_p,
                max_price=max_p,
                avg_price=int(avg_p) if avg_p else None,
                median_price=median_p,
                total_valuation=int(sum_p) if sum_p else 0
            )

    async def compute_and_update_market_averages(self) -> int:
        """Расчет средней цены по поисковым группам и разметка выгодных предложений (Hot Deals)"""
        updated_count = 0
        async with self.connection() as db:
            # Группировка по search_query_id
            cursor = await db.execute("""
                SELECT membership.search_id, AVG(i.price) as avg_price
                FROM item_searches membership JOIN items i ON i.id=membership.item_id
                WHERE i.price IS NOT NULL AND i.price > 0 AND COALESCE(i.is_closed,0)=0 AND COALESCE(i.is_hidden,0)=0
                GROUP BY membership.search_id
            """)
            groups = await cursor.fetchall()

            for sq_id, avg_p in groups:
                if not avg_p:
                    continue
                avg_int = int(avg_p)
                # Помечаем hot deal, если цена на 15% ниже средней
                hot_thresh = int(avg_int * 0.85)
                cur_upd = await db.execute("""
                    UPDATE items SET
                        market_avg_price = ?,
                        is_hot_deal = CASE WHEN price <= ? THEN 1 ELSE 0 END
                    WHERE search_query_id = ?
                """, (avg_int, hot_thresh, sq_id))
                updated_count += cur_upd.rowcount

            await db.commit()
        return updated_count

    async def get_price_trend_history(self, days: int = 14) -> List[Dict[str, Any]]:
        """История средних цен и падений цен по дням для аналитического графика"""
        result: List[Dict[str, Any]] = []
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT
                    DATE(created_at) as log_date,
                    COUNT(*) as changes_count,
                    AVG(new_price) as avg_new_price,
                    SUM(CASE WHEN delta < 0 THEN 1 ELSE 0 END) as drops_count,
                    AVG(ABS(delta)) as avg_discount
                FROM price_history
                WHERE created_at >= DATE('now', '-' || ? || ' days')
                GROUP BY DATE(created_at)
                ORDER BY log_date ASC
            """, (days,))
            rows = await cursor.fetchall()
            for r in rows:
                result.append({
                    "date": r["log_date"],
                    "changes_count": r["changes_count"],
                    "avg_price": int(r["avg_new_price"] or 0),
                    "drops_count": r["drops_count"],
                    "avg_discount": int(r["avg_discount"] or 0)
                })
        return result

    async def toggle_item_favorite(self, item_id: str, is_favorite: Optional[bool] = None) -> bool:
        """Добавление/удаление товара в избранное"""
        async with self.connection() as db:
            if is_favorite is None:
                await db.execute("UPDATE items SET is_favorite = CASE WHEN is_favorite = 1 THEN 0 ELSE 1 END WHERE id = ?", (item_id,))
            else:
                await db.execute("UPDATE items SET is_favorite = ? WHERE id = ?", (1 if is_favorite else 0, item_id))
            await db.commit()
            c = await db.execute("SELECT is_favorite FROM items WHERE id = ?", (item_id,))
            r = await c.fetchone()
            return bool(r[0]) if r else False

    async def toggle_item_hidden(self, item_id: str, is_hidden: Optional[bool] = None) -> bool:
        """Скрытие/восстановление товара из каталога"""
        async with self.connection() as db:
            if is_hidden is None:
                await db.execute("UPDATE items SET is_hidden = CASE WHEN is_hidden = 1 THEN 0 ELSE 1 END WHERE id = ?", (item_id,))
            else:
                await db.execute("UPDATE items SET is_hidden = ? WHERE id = ?", (1 if is_hidden else 0, item_id))
            await db.commit()
            c = await db.execute("SELECT is_hidden FROM items WHERE id = ?", (item_id,))
            r = await c.fetchone()
            return bool(r[0]) if r else False

    async def batch_process_items(
        self,
        item_ids: List[str],
        action: str,
        search_query_id: Optional[int] = None
    ) -> int:
        """Массовые операции над выбранными товарами"""
        if not item_ids:
            return 0
        async with self.connection() as db:
            placeholders = ",".join("?" for _ in item_ids)
            if action == "delete":
                await db.execute(f"DELETE FROM price_history WHERE item_id IN ({placeholders})", item_ids)
                await db.execute(f"DELETE FROM sent_notifications WHERE item_id IN ({placeholders})", item_ids)
                cur = await db.execute(f"DELETE FROM items WHERE id IN ({placeholders})", item_ids)
            elif action == "favorite":
                cur = await db.execute(f"UPDATE items SET is_favorite = 1 WHERE id IN ({placeholders})", item_ids)
            elif action == "unfavorite":
                cur = await db.execute(f"UPDATE items SET is_favorite = 0 WHERE id IN ({placeholders})", item_ids)
            elif action == "hide":
                cur = await db.execute(f"UPDATE items SET is_hidden = 1 WHERE id IN ({placeholders})", item_ids)
            elif action == "assign_search" and search_query_id is not None:
                cur = await db.execute(f"UPDATE items SET search_query_id = ? WHERE id IN ({placeholders})", [search_query_id] + item_ids)
                await db.executemany('INSERT OR IGNORE INTO item_searches (item_id,search_id,last_seen_at) SELECT id,?,? FROM items WHERE id=?', [(search_query_id, datetime.now().isoformat(), item_id) for item_id in item_ids])
            else:
                return 0
            await db.commit()
            return cur.rowcount

    # ==========================================================================
    # ЧЕРНЫЙ СПИСОК ПРОДАВЦОВ
    # ==========================================================================

    async def add_seller_to_blacklist(self, seller_name: str, reason: str = "") -> bool:
        """Добавление продавца в черный список"""
        seller_name = seller_name.strip()
        if not seller_name:
            return False
        async with self.connection() as db:
            await db.execute("""
                INSERT INTO seller_blacklist (seller_name, reason, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(seller_name) DO UPDATE SET reason = excluded.reason
            """, (seller_name, reason, datetime.now()))
            await db.commit()
            return True

    async def remove_seller_from_blacklist(self, seller_name: str) -> bool:
        """Удаление продавца из черного списка"""
        async with self.connection() as db:
            cur = await db.execute("DELETE FROM seller_blacklist WHERE seller_name = ?", (seller_name.strip(),))
            await db.commit()
            return cur.rowcount > 0

    async def get_blacklisted_sellers(self) -> List[str]:
        """Список заблокированных продавцов"""
        async with self.connection() as db:
            cursor = await db.execute("SELECT seller_name FROM seller_blacklist")
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

    # ==========================================================================
    # ЖУРНАЛ АУДИТА ДЕЙСТВИЙ (AUDIT LOG)
    # ==========================================================================

    async def log_audit(
        self,
        action: str,
        username: str = "system",
        details: str = "",
        ip_address: str = "",
        user_id: Optional[int] = None
    ) -> None:
        """Запись действия пользователя или системы в журнал безопасности"""
        try:
            async with self.connection() as db:
                await db.execute("""
                    INSERT INTO audit_logs (user_id, username, action, details, ip_address, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (user_id, username, action, details, ip_address, datetime.now()))
                await db.commit()
        except Exception:
            pass

    async def get_audit_logs(self, limit: int = 100) -> List[AuditLogEntry]:
        """Получение последних записей журнала аудита"""
        logs: List[AuditLogEntry] = []
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT id, user_id, username, action, details, ip_address, created_at
                FROM audit_logs ORDER BY id DESC LIMIT ?
            """, (limit,))
            rows = await cursor.fetchall()
            for r in rows:
                logs.append(AuditLogEntry(
                    id=r["id"],
                    user_id=r["user_id"],
                    username=r["username"],
                    action=r["action"],
                    details=r["details"],
                    ip_address=r["ip_address"],
                    created_at=datetime.fromisoformat(str(r["created_at"])) if r["created_at"] else datetime.now()
                ))
        return logs

    # ==========================================================================
    # СИСТЕМНАЯ ТЕЛЕМЕТРИЯ
    # ==========================================================================

    async def get_system_telemetry(self) -> SystemTelemetry:
        """Сбор сводки телеметрии о состоянии базы и системы"""
        stats = await self.get_stats()
        db_size = self.db_path.stat().st_size if self.db_path.exists() else 0
        
        return SystemTelemetry(
            total_items=stats["total_items"],
            total_searches=stats["total_searches"],
            active_searches=stats["active_searches"],
            total_price_drops=stats["total_price_drops"],
            db_size_kb=round(db_size / 1024),
            server_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )

    # ==========================================================================
    # ДЕДУПЛИКАЦИЯ И ХЕШИРОВАНИЕ КОНТЕНТА
    # ==========================================================================

    async def compute_content_hash(self, item: AvitoItem) -> str:
        """Расчет MD5 хеша по заголовку, первым 100 символам описания и цене"""
        desc_sample = (item.description or "")[:100].strip().lower()
        title_clean = (item.title or "").strip().lower()
        price_str = str(item.price or 0)
        raw = f"{title_clean}|{desc_sample}|{price_str}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    async def find_duplicate(self, content_hash: str, exclude_id: str = "") -> Optional[AvitoItem]:
        """Поиск возможного дубликата объявления по content_hash"""
        if not content_hash:
            return None
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT * FROM items
                WHERE content_hash = ? AND id != ?
                  AND (is_hidden = 0 OR is_hidden IS NULL)
                LIMIT 1
            """, (content_hash, exclude_id))
            row = await cursor.fetchone()
            if row:
                return self._row_to_item(row)
        return None

    # ==========================================================================
    # ПОЛЬЗОВАТЕЛЬСКИЕ ПРАВИЛА СКОРИНГА (СТОП-СЛОВА И БУСТЕРЫ)
    # ==========================================================================

    async def get_custom_scoring_rules(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """Получение списка пользовательских правил скоринга"""
        rules: List[Dict[str, Any]] = []
        query = "SELECT * FROM custom_scoring_rules"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY id ASC"

        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query)
            rows = await cursor.fetchall()
            for r in rows:
                rules.append({
                    "id": r["id"],
                    "pattern": r["pattern"],
                    "label": r["label"],
                    "score_delta": r["score_delta"],
                    "is_active": bool(r["is_active"]),
                    "created_at": r["created_at"]
                })
        return rules

    async def add_custom_scoring_rule(self, pattern: str, label: str, score_delta: int) -> int:
        """Добавление нового правила скоринга (стоп-слова / бустера)"""
        async with self.connection() as db:
            cursor = await db.execute("""
                INSERT INTO custom_scoring_rules (pattern, label, score_delta, is_active, created_at)
                VALUES (?, ?, ?, 1, ?)
            """, (pattern.strip(), label.strip(), score_delta, datetime.now()))
            await db.commit()
            rule_id = cursor.lastrowid or 0

        # Перезагружаем правила в движок скоринга
        active_rules = await self.get_custom_scoring_rules(active_only=True)
        deal_scoring_engine.load_custom_rules(active_rules)
        return rule_id

    async def delete_custom_scoring_rule(self, rule_id: int) -> bool:
        """Удаление правила скоринга"""
        async with self.connection() as db:
            cursor = await db.execute("DELETE FROM custom_scoring_rules WHERE id = ?", (rule_id,))
            await db.commit()
            ok = cursor.rowcount > 0

        active_rules = await self.get_custom_scoring_rules(active_only=True)
        deal_scoring_engine.load_custom_rules(active_rules)
        return ok

    async def toggle_custom_scoring_rule(self, rule_id: int) -> bool:
        """Включение/выключение правила скоринга"""
        async with self.connection() as db:
            cursor = await db.execute("""
                UPDATE custom_scoring_rules SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END WHERE id = ?
            """, (rule_id,))
            await db.commit()
            ok = cursor.rowcount > 0

        active_rules = await self.get_custom_scoring_rules(active_only=True)
        deal_scoring_engine.load_custom_rules(active_rules)
        return ok

    # ==========================================================================
    # WEBHOOK НАСТРОЙКИ
    # ==========================================================================

    async def get_webhook_settings(self) -> WebhookSettings:
        """Получение настроек Webhook"""
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM webhook_settings WHERE id = 1")
            r = await cursor.fetchone()
            if r:
                return WebhookSettings(
                    id=r["id"],
                    url=r["url"] or "",
                    enabled=bool(r["enabled"]),
                    send_new=bool(r["send_new"]),
                    send_drops=bool(r["send_drops"]),
                    min_deal_score=int(r["min_deal_score"] or 0),
                    secret=r["secret"] or "",
                    updated_at=datetime.fromisoformat(str(r["updated_at"])) if r["updated_at"] else None
                )
            return WebhookSettings()

    async def save_webhook_settings(self, settings: WebhookSettings) -> WebhookSettings:
        """Сохранение настроек Webhook"""
        now = datetime.now()
        async with self.connection() as db:
            await db.execute("""
                INSERT INTO webhook_settings (id, url, enabled, send_new, send_drops, min_deal_score, secret, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    url = excluded.url,
                    enabled = excluded.enabled,
                    send_new = excluded.send_new,
                    send_drops = excluded.send_drops,
                    min_deal_score = excluded.min_deal_score,
                    secret = excluded.secret,
                    updated_at = excluded.updated_at
            """, (
                settings.url.strip(),
                1 if settings.enabled else 0,
                1 if settings.send_new else 0,
                1 if settings.send_drops else 0,
                settings.min_deal_score,
                settings.secret.strip(),
                now
            ))
            await db.commit()
        return await self.get_webhook_settings()

    # ==========================================================================
    # СТАТИСТИКА СКОРОСТИ РЫНКА (GEM/HOT LIFETIME)
    # ==========================================================================

    async def get_gem_lifetime_stats(self) -> Dict[str, Any]:
        """Расчет времени жизни (скорости выкупа) выгодных лотов GEM/HOT"""
        async with self.connection() as db:
            cursor = await db.execute("""
                SELECT
                    created_at, closed_at, deal_grade,
                    (julianday(closed_at) - julianday(created_at)) * 24.0 AS lifetime_hours
                FROM items
                WHERE deal_grade IN ('GEM', 'HOT')
                  AND is_closed = 1
                  AND closed_at IS NOT NULL
                  AND created_at IS NOT NULL
                  AND (julianday(closed_at) - julianday(created_at)) >= 0
                ORDER BY lifetime_hours ASC
            """)
            rows = await cursor.fetchall()

            if not rows:
                return {
                    "count": 0,
                    "avg_hours": 0.0,
                    "median_hours": 0.0,
                    "min_hours": 0.0,
                    "max_hours": 0.0
                }

            lifetimes = [r[3] for r in rows if r[3] is not None]
            count = len(lifetimes)
            if count == 0:
                return {"count": 0, "avg_hours": 0.0, "median_hours": 0.0, "min_hours": 0.0, "max_hours": 0.0}

            avg_h = sum(lifetimes) / count
            median_h = lifetimes[count // 2]
            min_h = min(lifetimes)
            max_h = max(lifetimes)

            return {
                "count": count,
                "avg_hours": round(avg_h, 1),
                "median_hours": round(median_h, 1),
                "min_hours": round(min_h, 1),
                "max_hours": round(max_h, 1)
            }


db = Database()
