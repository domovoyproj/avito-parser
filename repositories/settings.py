from datetime import datetime
from typing import Any, Dict, List, Optional
import aiosqlite
from config import config
from models import AISettings, TelegramChatSettings, WebhookSettings
from ai_scoring import deal_scoring_engine


class SettingsRepository:
    async def get_chat_settings(self, chat_id: int) -> TelegramChatSettings:
        """Получение персональных настроек уведомлений для чата Telegram"""
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM telegram_chat_settings WHERE chat_id = ?", (chat_id,)
            )
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
                    min_deal_score=int(r["min_deal_score"] or 0)
                    if "min_deal_score" in cols
                    else 0,
                    only_below_market=bool(r["only_below_market"])
                    if "only_below_market" in cols
                    else False,
                    below_market_pct=int(r["below_market_pct"] or 15)
                    if "below_market_pct" in cols
                    else 15,
                    updated_at=datetime.fromisoformat(str(r["updated_at"]))
                    if r["updated_at"]
                    else None,
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
                below_market_pct=15,
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
        below_market_pct: Optional[int] = None,
    ) -> TelegramChatSettings:
        """Обновление настроек уведомлений конкретного чата Telegram"""
        current = await self.get_chat_settings(chat_id)

        new_notify_new = notify_new if notify_new is not None else current.notify_new
        new_notify_drops = (
            notify_drops if notify_drops is not None else current.notify_drops
        )
        new_send_photos = (
            send_photos if send_photos is not None else current.send_photos
        )
        new_delivery_only = (
            delivery_only if delivery_only is not None else current.delivery_only
        )
        new_min_discount = (
            min_discount_pct
            if min_discount_pct is not None
            else current.min_discount_pct
        )
        new_min_deal_score = (
            min_deal_score if min_deal_score is not None else current.min_deal_score
        )
        new_only_below = (
            only_below_market
            if only_below_market is not None
            else current.only_below_market
        )
        new_below_pct = (
            below_market_pct
            if below_market_pct is not None
            else current.below_market_pct
        )

        async with self.connection() as db:
            await db.execute(
                """
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
            """,
                (
                    chat_id,
                    1 if new_notify_new else 0,
                    1 if new_notify_drops else 0,
                    1 if new_send_photos else 0,
                    1 if new_delivery_only else 0,
                    new_min_discount,
                    new_min_deal_score,
                    1 if new_only_below else 0,
                    new_below_pct,
                    datetime.now(),
                ),
            )
            await db.commit()

        return await self.get_chat_settings(chat_id)

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
                    prompt_template=r["prompt_template"]
                    if "prompt_template" in r.keys()
                    else None,
                    updated_at=datetime.fromisoformat(str(r["updated_at"]))
                    if r["updated_at"]
                    else None,
                )
            return AISettings(
                enabled=False,
                provider="deepseek",
                api_key="",
                model="deepseek-chat",
                api_base="https://api.deepseek.com",
            )

    async def save_ai_settings(self, settings: AISettings) -> AISettings:
        """Сохранение настроек AI-провайдера"""
        now = datetime.now()
        async with self.connection() as db:
            await db.execute(
                """
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
            """,
                (
                    1 if settings.enabled else 0,
                    settings.provider,
                    settings.api_key,
                    settings.model,
                    settings.api_base,
                    settings.prompt_template,
                    now,
                ),
            )
            await db.commit()
        return await self.get_ai_settings()

    async def add_seller_to_blacklist(self, seller_name: str, reason: str = "") -> bool:
        """Добавление продавца в черный список"""
        seller_name = seller_name.strip()
        if not seller_name:
            return False
        async with self.connection() as db:
            await db.execute(
                """
                INSERT INTO seller_blacklist (seller_name, reason, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(seller_name) DO UPDATE SET reason = excluded.reason
            """,
                (seller_name, reason, datetime.now()),
            )
            await db.commit()
            return True

    async def remove_seller_from_blacklist(self, seller_name: str) -> bool:
        """Удаление продавца из черного списка"""
        async with self.connection() as db:
            cur = await db.execute(
                "DELETE FROM seller_blacklist WHERE seller_name = ?",
                (seller_name.strip(),),
            )
            await db.commit()
            return cur.rowcount > 0

    async def get_blacklisted_sellers(self) -> List[str]:
        """Список заблокированных продавцов"""
        async with self.connection() as db:
            cursor = await db.execute("SELECT seller_name FROM seller_blacklist")
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

    async def get_custom_scoring_rules(
        self, active_only: bool = True
    ) -> List[Dict[str, Any]]:
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
                rules.append(
                    {
                        "id": r["id"],
                        "pattern": r["pattern"],
                        "label": r["label"],
                        "score_delta": r["score_delta"],
                        "is_active": bool(r["is_active"]),
                        "created_at": r["created_at"],
                    }
                )
        return rules

    async def add_custom_scoring_rule(
        self, pattern: str, label: str, score_delta: int
    ) -> int:
        """Добавление нового правила скоринга (стоп-слова / бустера)"""
        async with self.connection() as db:
            cursor = await db.execute(
                """
                INSERT INTO custom_scoring_rules (pattern, label, score_delta, is_active, created_at)
                VALUES (?, ?, ?, 1, ?)
            """,
                (pattern.strip(), label.strip(), score_delta, datetime.now()),
            )
            await db.commit()
            rule_id = cursor.lastrowid or 0

        # Перезагружаем правила в движок скоринга
        active_rules = await self.get_custom_scoring_rules(active_only=True)
        deal_scoring_engine.load_custom_rules(active_rules)
        return rule_id

    async def delete_custom_scoring_rule(self, rule_id: int) -> bool:
        """Удаление правила скоринга"""
        async with self.connection() as db:
            cursor = await db.execute(
                "DELETE FROM custom_scoring_rules WHERE id = ?", (rule_id,)
            )
            await db.commit()
            ok = cursor.rowcount > 0

        active_rules = await self.get_custom_scoring_rules(active_only=True)
        deal_scoring_engine.load_custom_rules(active_rules)
        return ok

    async def toggle_custom_scoring_rule(self, rule_id: int) -> bool:
        """Включение/выключение правила скоринга"""
        async with self.connection() as db:
            cursor = await db.execute(
                """
                UPDATE custom_scoring_rules SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END WHERE id = ?
            """,
                (rule_id,),
            )
            await db.commit()
            ok = cursor.rowcount > 0

        active_rules = await self.get_custom_scoring_rules(active_only=True)
        deal_scoring_engine.load_custom_rules(active_rules)
        return ok

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
                    updated_at=datetime.fromisoformat(str(r["updated_at"]))
                    if r["updated_at"]
                    else None,
                )
            return WebhookSettings()

    async def save_webhook_settings(self, settings: WebhookSettings) -> WebhookSettings:
        """Сохранение настроек Webhook"""
        now = datetime.now()
        async with self.connection() as db:
            await db.execute(
                """
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
            """,
                (
                    settings.url.strip(),
                    1 if settings.enabled else 0,
                    1 if settings.send_new else 0,
                    1 if settings.send_drops else 0,
                    settings.min_deal_score,
                    settings.secret.strip(),
                    now,
                ),
            )
            await db.commit()
        return await self.get_webhook_settings()
