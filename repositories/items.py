import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import aiosqlite
from config import config
from models import AvitoItem, PriceChange, SellerInfo
from ai_scoring import deal_scoring_engine


class ItemsRepository:
    async def save_item(
        self, item: AvitoItem, *, market_stats: Optional[Tuple[Optional[int], Optional[int]]] = None
    ) -> Tuple[AvitoItem, bool, bool, Optional[int]]:
        """
        Сохраняет или обновляет объявление.
        Возвращает (item, is_new, is_price_drop, previous_price).
        """
        is_new = False
        is_new_membership = False
        is_price_drop = False
        prev_price = None

        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT id, price, old_price, ai_summary FROM items WHERE id = ?",
                (item.id,),
            )
            row = await cursor.fetchone()

            # Автоматический расчет скоринга сделки
            market_med, market_avg = market_stats if market_stats is not None else await self.get_search_market_stats(item.search_query_id)
            deal_score, deal_grade, deal_reasons, detected_flaws = (
                deal_scoring_engine.evaluate_item(
                    item, market_median=market_med, market_avg=market_avg
                )
            )
            item.deal_score = deal_score
            item.deal_grade = deal_grade
            item.deal_reasons = deal_reasons
            item.detected_flaws = detected_flaws
            if deal_grade in ("GEM", "HOT"):
                item.is_hot_deal = True

            images_json = json.dumps(item.images, ensure_ascii=False)
            params_json = json.dumps(item.params, ensure_ascii=False)
            seller_json = json.dumps(
                item.seller.model_dump() if item.seller else None, ensure_ascii=False
            )
            deal_reasons_json = json.dumps(item.deal_reasons, ensure_ascii=False)
            detected_flaws_json = json.dumps(item.detected_flaws, ensure_ascii=False)
            content_hash = await self.compute_content_hash(item)
            item.content_hash = content_hash
            now = datetime.now()

            if row is None:
                is_new = True
                await db.execute(
                    """
                    INSERT INTO items (
                        id, title, price, old_price, price_string, url, address, metro,
                        published_at, description, main_image, images_json, params_json,
                        seller_json, delivery_available, is_vip, category, search_query_id,
                        deal_score, deal_grade, deal_reasons_json, ai_summary, detected_flaws_json,
                        is_hot_deal, is_reserved, is_closed, content_hash, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        item.id,
                        item.title,
                        item.price,
                        item.old_price,
                        item.price_string,
                        item.url,
                        item.address,
                        item.metro,
                        item.published_at,
                        item.description,
                        item.main_image,
                        images_json,
                        params_json,
                        seller_json,
                        item.delivery_available,
                        item.is_vip,
                        item.category,
                        item.search_query_id,
                        item.deal_score,
                        item.deal_grade,
                        deal_reasons_json,
                        item.ai_summary,
                        detected_flaws_json,
                        1 if item.is_hot_deal else 0,
                        1 if item.is_reserved else 0,
                        1 if item.is_closed else 0,
                        content_hash,
                        now,
                        now,
                    ),
                )
            else:
                prev_price = row["price"]
                existing_ai_summary = row["ai_summary"]
                if existing_ai_summary and not item.ai_summary:
                    item.ai_summary = existing_ai_summary

                # Проверка изменения цены
                if (
                    item.price is not None
                    and prev_price is not None
                    and item.price != prev_price
                ):
                    delta = item.price - prev_price
                    item.old_price = prev_price
                    if delta < 0:
                        is_price_drop = True

                    await db.execute(
                        """
                        INSERT INTO price_history (item_id, old_price, new_price, delta, created_at)
                        VALUES (?, ?, ?, ?, ?)
                    """,
                        (item.id, prev_price, item.price, delta, now),
                    )

                await db.execute(
                    """
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
                """,
                    (
                        item.title,
                        item.price,
                        item.old_price,
                        item.price_string,
                        item.url,
                        item.address,
                        item.metro,
                        item.published_at,
                        item.description,
                        item.main_image,
                        images_json,
                        params_json,
                        seller_json,
                        item.delivery_available,
                        item.is_vip,
                        item.category,
                        item.deal_score,
                        item.deal_grade,
                        deal_reasons_json,
                        item.ai_summary,
                        detected_flaws_json,
                        1 if item.is_hot_deal else 0,
                        1 if item.is_reserved else 0,
                        1 if item.is_closed else 0,
                        content_hash,
                        now,
                        item.id,
                    ),
                )
            await db.execute(
                "UPDATE items SET score_version=?,score_confidence=? WHERE id=?",
                (item.score_version, item.score_confidence, item.id),
            )
            if item.search_query_id is not None:
                membership = await (await db.execute(
                    "SELECT 1 FROM item_searches WHERE item_id=? AND search_id=?",
                    (item.id, item.search_query_id),
                )).fetchone()
                is_new_membership = membership is None
                await db.execute(
                    "INSERT INTO item_searches VALUES (?, ?, ?) ON CONFLICT(item_id, search_id) DO UPDATE SET last_seen_at=excluded.last_seen_at",
                    (item.id, item.search_query_id, now.isoformat()),
                )
            if is_new or is_new_membership or is_price_drop:
                search_name = "Поиск"
                if item.search_query_id is not None:
                    search_row = await (
                        await db.execute(
                            "SELECT name FROM searches WHERE id=?",
                            (item.search_query_id,),
                        )
                    ).fetchone()
                    if search_row:
                        search_name = search_row[0]
                event_type = "new" if is_new or is_new_membership else "price_drop"
                payload = json.dumps(
                    {
                        "item": item.model_dump(mode="json"),
                        "search_name": search_name,
                        "type": event_type,
                    },
                    ensure_ascii=False,
                )
                event = (
                    f"{item.id}:{item.search_query_id}:{event_type}:{now.isoformat()}"
                )
                if config.telegram.bot_token:
                    for chat in set(config.telegram.admin_chat_ids):
                        await db.execute(
                            "INSERT OR IGNORE INTO notification_outbox(event_key, channel, destination, payload, created_at) VALUES (?, 'telegram', ?, ?, ?)",
                            (f"{event}:telegram:{chat}", str(chat), payload, now.timestamp()),
                        )
                webhook = await (
                    await db.execute(
                        "SELECT url, enabled, send_new, send_drops FROM webhook_settings WHERE id=1"
                    )
                ).fetchone()
                if (
                    webhook
                    and webhook[1]
                    and webhook[0]
                    and webhook[2 if event_type == "new" else 3]
                ):
                    await db.execute(
                        "INSERT OR IGNORE INTO notification_outbox(event_key, channel, destination, payload, created_at) VALUES (?, 'webhook', ?, ?, ?)",
                        (f"{event}:webhook", webhook[0], payload, now.timestamp()),
                    )
                if is_price_drop and item.price is not None and config.telegram.bot_token:
                    await self.enqueue_watchlist_alerts(db, item, item.price, json.loads(payload), now.timestamp())
            await db.commit()
        return item, is_new, is_price_drop, prev_price

    async def save_items(self, items: List[AvitoItem]) -> Dict[str, Any]:
        """Пакетное сохранение объявлений с подсчетом статистики"""
        new_count = 0
        price_drop_count = 0
        price_changes: List[PriceChange] = []
        new_items: List[AvitoItem] = []
        # One stable market snapshot per search avoids rescanning the whole catalogue for every card.
        markets = {search_id: await self.get_search_market_stats(search_id) for search_id in {item.search_query_id for item in items}}

        for item in items:
            saved_item, is_new, is_price_drop, prev_price = await self.save_item(item, market_stats=markets[item.search_query_id])
            if is_new:
                new_count += 1
                new_items.append(saved_item)
            elif is_price_drop and prev_price is not None and item.price is not None:
                price_drop_count += 1
                price_changes.append(
                    PriceChange(
                        item_id=item.id,
                        item_title=item.title,
                        old_price=prev_price,
                        new_price=item.price,
                        delta=item.price - prev_price,
                        url=item.url,
                    )
                )

        return {
            "total": len(items),
            "new_count": new_count,
            "new_items": new_items,
            "price_drop_count": price_drop_count,
            "price_changes": price_changes,
        }

    async def cleanup_stale_items(
        self, search_query_id: int, active_item_ids: List[str]
    ) -> int:
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
                WHERE EXISTS (SELECT 1 FROM item_searches m WHERE m.item_id=items.id AND m.search_id=?)
                  AND NOT EXISTS (SELECT 1 FROM item_searches m WHERE m.item_id=items.id AND m.search_id<>?)
                  AND id NOT IN ({placeholders})
                  AND (is_closed = 0 OR is_closed IS NULL)
            """
            params = [now, now, search_query_id, search_query_id] + list(active_item_ids)
            cursor = await db.execute(sql, params)
            await db.commit()
            return cursor.rowcount

    async def get_items(
        self,
        search_query_id: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
        min_price: Optional[int] = None,
        max_price: Optional[int] = None,
    ) -> List[AvitoItem]:
        """Получение списка объявлений из базы данных с фильтрацией"""
        items, _ = await self.get_items_filtered(
            search_query_id=search_query_id,
            min_price=min_price,
            max_price=max_price,
            limit=limit,
            offset=offset,
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
        offset: int = 0,
        user_id: Optional[int] = None,
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
            if user_id is None:
                where_clauses.append("is_favorite = 1")
            else:
                where_clauses.append("EXISTS (SELECT 1 FROM watchlist_items wi JOIN watchlists w ON w.id=wi.watchlist_id WHERE wi.item_id=items.id AND w.user_id=? AND w.is_default=1)")
                params.append(user_id)

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
                    where_clauses.append(
                        "EXISTS (SELECT 1 FROM item_searches membership WHERE membership.item_id=items.id AND membership.search_id=?)"
                    )
                    params.append(int(search_query_id))
                except (ValueError, TypeError):
                    pass

        if query:
            where_clauses.append(
                "(title LIKE ? OR description LIKE ? OR address LIKE ? OR params_json LIKE ?)"
            )
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

        if user_id is not None and items:
            favorites = await self.personal_favorite_ids(user_id, [item.id for item in items])
            for item in items:
                item.is_favorite = item.id in favorites

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
            await db.execute(
                "DELETE FROM sent_notifications WHERE item_id = ?", (item_id,)
            )
            cursor = await db.execute("DELETE FROM items WHERE id = ?", (item_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def delete_items_batch(self, item_ids: List[str]) -> int:
        """Пакетное удаление объявлений"""
        if not item_ids:
            return 0
        async with self.connection() as db:
            placeholders = ",".join("?" for _ in item_ids)
            await db.execute(
                f"DELETE FROM price_history WHERE item_id IN ({placeholders})", item_ids
            )
            await db.execute(
                f"DELETE FROM sent_notifications WHERE item_id IN ({placeholders})",
                item_ids,
            )
            cursor = await db.execute(
                f"DELETE FROM items WHERE id IN ({placeholders})", item_ids
            )
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

    async def is_notification_sent(
        self, item_id: str, search_query_id: Optional[int], notif_type: str
    ) -> bool:
        """Проверка, было ли уже отправлено уведомление данного типа"""
        async with self.connection() as db:
            cursor = await db.execute(
                """
                SELECT 1 FROM sent_notifications
                WHERE item_id = ? AND search_query_id IS ? AND notification_type = ?
            """,
                (item_id, search_query_id, notif_type),
            )
            return await cursor.fetchone() is not None

    async def mark_notification_sent(
        self, item_id: str, search_query_id: Optional[int], notif_type: str
    ) -> None:
        """Фиксация отправленного уведомления в БД"""
        async with self.connection() as db:
            await db.execute(
                """
                INSERT OR IGNORE INTO sent_notifications (item_id, search_query_id, notification_type, sent_at)
                VALUES (?, ?, ?, ?)
            """,
                (item_id, search_query_id, notif_type, datetime.now()),
            )
            await db.commit()

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

        deal_score = (
            int(row["deal_score"])
            if "deal_score" in cols and row["deal_score"] is not None
            else 50
        )
        deal_grade = (
            str(row["deal_grade"])
            if "deal_grade" in cols and row["deal_grade"]
            else "FAIR"
        )
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
            market_avg_price=row["market_avg_price"]
            if "market_avg_price" in cols
            else None,
            is_hot_deal=bool(row["is_hot_deal"]) if "is_hot_deal" in cols else False,
            deal_score=deal_score,
            score_version=row["score_version"]
            if "score_version" in cols
            else "legacy-1",
            score_confidence=row["score_confidence"]
            if "score_confidence" in cols
            else 0,
            deal_grade=deal_grade,
            deal_reasons=deal_reasons,
            ai_summary=ai_summary,
            detected_flaws=detected_flaws,
            content_hash=row["content_hash"] if "content_hash" in cols else None,
            closed_at=datetime.fromisoformat(str(row["closed_at"]))
            if ("closed_at" in cols and row["closed_at"])
            else None,
            created_at=datetime.fromisoformat(str(row["created_at"]))
            if row["created_at"]
            else datetime.now(),
            updated_at=datetime.fromisoformat(str(row["updated_at"]))
            if row["updated_at"]
            else datetime.now(),
        )

    async def update_item_ai_summary(self, item_id: str, summary: str) -> bool:
        """Сохранение сгенерированного AI-резюме для товара"""
        async with self.connection() as db:
            cursor = await db.execute(
                "UPDATE items SET ai_summary = ?, updated_at = ? WHERE id = ?",
                (summary, datetime.now(), item_id),
            )
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
                it.is_hot_deal = grade in ("GEM", "HOT")

                reasons_json = json.dumps(reasons, ensure_ascii=False)
                flaws_json = json.dumps(flaws, ensure_ascii=False)

                await db.execute(
                    """
                    UPDATE items SET
                        deal_score = ?,
                        deal_grade = ?,
                        deal_reasons_json = ?,
                        detected_flaws_json = ?,
                        is_hot_deal = ?,
                        score_version = ?,
                        score_confidence = ?
                    WHERE id = ?
                """,
                    (
                        score,
                        grade,
                        reasons_json,
                        flaws_json,
                        1 if it.is_hot_deal else 0,
                        it.score_version,
                        it.score_confidence,
                        it.id,
                    ),
                )
            await db.commit()

        return len(items_to_update)

    async def get_top_deal_items(self, limit: int = 6) -> List[AvitoItem]:
        """Получение топ-товаров по AI Score (только доступные Gems & Hot deals без брони)"""
        items: List[AvitoItem] = []
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM items
                WHERE (is_hidden = 0 OR is_hidden IS NULL)
                  AND (is_closed = 0 OR is_closed IS NULL)
                  AND (is_reserved = 0 OR is_reserved IS NULL)
                  AND deal_score >= 70
                ORDER BY deal_score DESC, created_at DESC
                LIMIT ?
            """,
                (limit,),
            )
            rows = await cursor.fetchall()
            for r in rows:
                items.append(self._row_to_item(r))
        return items

    async def toggle_item_favorite(
        self, item_id: str, is_favorite: Optional[bool] = None
    ) -> bool:
        """Добавление/удаление товара в избранное"""
        async with self.connection() as db:
            if is_favorite is None:
                await db.execute(
                    "UPDATE items SET is_favorite = CASE WHEN is_favorite = 1 THEN 0 ELSE 1 END WHERE id = ?",
                    (item_id,),
                )
            else:
                await db.execute(
                    "UPDATE items SET is_favorite = ? WHERE id = ?",
                    (1 if is_favorite else 0, item_id),
                )
            await db.commit()
            c = await db.execute(
                "SELECT is_favorite FROM items WHERE id = ?", (item_id,)
            )
            r = await c.fetchone()
            return bool(r[0]) if r else False

    async def toggle_item_hidden(
        self, item_id: str, is_hidden: Optional[bool] = None
    ) -> bool:
        """Скрытие/восстановление товара из каталога"""
        async with self.connection() as db:
            if is_hidden is None:
                await db.execute(
                    "UPDATE items SET is_hidden = CASE WHEN is_hidden = 1 THEN 0 ELSE 1 END WHERE id = ?",
                    (item_id,),
                )
            else:
                await db.execute(
                    "UPDATE items SET is_hidden = ? WHERE id = ?",
                    (1 if is_hidden else 0, item_id),
                )
            await db.commit()
            c = await db.execute("SELECT is_hidden FROM items WHERE id = ?", (item_id,))
            r = await c.fetchone()
            return bool(r[0]) if r else False

    async def batch_process_items(
        self, item_ids: List[str], action: str, search_query_id: Optional[int] = None
    ) -> int:
        """Массовые операции над выбранными товарами"""
        if not item_ids:
            return 0
        async with self.connection() as db:
            placeholders = ",".join("?" for _ in item_ids)
            if action == "delete":
                await db.execute(
                    f"DELETE FROM price_history WHERE item_id IN ({placeholders})",
                    item_ids,
                )
                await db.execute(
                    f"DELETE FROM sent_notifications WHERE item_id IN ({placeholders})",
                    item_ids,
                )
                cur = await db.execute(
                    f"DELETE FROM items WHERE id IN ({placeholders})", item_ids
                )
            elif action == "favorite":
                cur = await db.execute(
                    f"UPDATE items SET is_favorite = 1 WHERE id IN ({placeholders})",
                    item_ids,
                )
            elif action == "unfavorite":
                cur = await db.execute(
                    f"UPDATE items SET is_favorite = 0 WHERE id IN ({placeholders})",
                    item_ids,
                )
            elif action == "hide":
                cur = await db.execute(
                    f"UPDATE items SET is_hidden = 1 WHERE id IN ({placeholders})",
                    item_ids,
                )
            elif action == "assign_search" and search_query_id is not None:
                cur = await db.execute(
                    f"UPDATE items SET search_query_id = ? WHERE id IN ({placeholders})",
                    [search_query_id] + item_ids,
                )
                await db.executemany(
                    "INSERT OR IGNORE INTO item_searches (item_id,search_id,last_seen_at) SELECT id,?,? FROM items WHERE id=?",
                    [
                        (search_query_id, datetime.now().isoformat(), item_id)
                        for item_id in item_ids
                    ],
                )
            else:
                return 0
            await db.commit()
            return cur.rowcount

    async def compute_content_hash(self, item: AvitoItem) -> str:
        """Расчет MD5 хеша по заголовку, первым 100 символам описания и цене"""
        desc_sample = (item.description or "")[:100].strip().lower()
        title_clean = (item.title or "").strip().lower()
        price_str = str(item.price or 0)
        raw = f"{title_clean}|{desc_sample}|{price_str}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    async def find_duplicate(
        self, content_hash: str, exclude_id: str = ""
    ) -> Optional[AvitoItem]:
        """Поиск возможного дубликата объявления по content_hash"""
        if not content_hash:
            return None
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM items
                WHERE content_hash = ? AND id != ?
                  AND (is_hidden = 0 OR is_hidden IS NULL)
                LIMIT 1
            """,
                (content_hash, exclude_id),
            )
            row = await cursor.fetchone()
            if row:
                return self._row_to_item(row)
        return None
