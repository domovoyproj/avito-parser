from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import aiosqlite
from models import AuditLogEntry, PriceStats, SystemTelemetry


class AnalyticsRepository:
    async def get_price_history(
        self, item_id: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
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
                history.append(
                    {
                        "id": r["id"],
                        "item_id": r["item_id"],
                        "old_price": r["old_price"],
                        "new_price": r["new_price"],
                        "delta": r["delta"],
                        "created_at": r["created_at"],
                    }
                )
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
                discount_pct = (
                    round((abs(r["delta"]) / r["old_price"]) * 100, 1)
                    if r["old_price"] > 0
                    else 0
                )
                drops.append(
                    {
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
                        "dropped_at": r["dropped_at"],
                    }
                )
        return drops

    async def get_stats(self) -> Dict[str, Any]:
        """Статистика базы данных"""
        async with self.connection() as db:
            items_res = await (
                await db.execute(
                    "SELECT COUNT(*) FROM items WHERE (is_hidden = 0 OR is_hidden IS NULL) AND (is_closed = 0 OR is_closed IS NULL) AND (is_reserved = 0 OR is_reserved IS NULL)"
                )
            ).fetchone()
            searches_res = await (
                await db.execute("SELECT COUNT(*) FROM searches")
            ).fetchone()
            active_searches_res = await (
                await db.execute("SELECT COUNT(*) FROM searches WHERE enabled = 1")
            ).fetchone()
            price_drops_res = await (
                await db.execute(
                    "SELECT COUNT(*) FROM price_history ph JOIN items i ON ph.item_id = i.id WHERE ph.delta < 0 AND (i.is_hidden = 0 OR i.is_hidden IS NULL) AND (i.is_closed = 0 OR i.is_closed IS NULL) AND (i.is_reserved = 0 OR i.is_reserved IS NULL)"
                )
            ).fetchone()

            # Новых товаров сегодня
            today_items = await (
                await db.execute(
                    "SELECT COUNT(*) FROM items WHERE DATE(created_at) = DATE('now') AND (is_hidden = 0 OR is_hidden IS NULL) AND (is_closed = 0 OR is_closed IS NULL) AND (is_reserved = 0 OR is_reserved IS NULL)"
                )
            ).fetchone()
            # Скидок сегодня
            today_drops = await (
                await db.execute(
                    "SELECT COUNT(*) FROM price_history ph JOIN items i ON ph.item_id = i.id WHERE ph.delta < 0 AND DATE(ph.created_at) = DATE('now') AND (i.is_hidden = 0 OR i.is_hidden IS NULL) AND (i.is_closed = 0 OR i.is_closed IS NULL) AND (i.is_reserved = 0 OR i.is_reserved IS NULL)"
                )
            ).fetchone()

            # Статистика выгодных лотов (только доступные)
            gems_res = await (
                await db.execute(
                    "SELECT COUNT(*) FROM items WHERE deal_grade = 'GEM' AND (is_hidden = 0 OR is_hidden IS NULL) AND (is_closed = 0 OR is_closed IS NULL) AND (is_reserved = 0 OR is_reserved IS NULL)"
                )
            ).fetchone()
            hot_res = await (
                await db.execute(
                    "SELECT COUNT(*) FROM items WHERE deal_grade IN ('GEM', 'HOT') AND (is_hidden = 0 OR is_hidden IS NULL) AND (is_closed = 0 OR is_closed IS NULL) AND (is_reserved = 0 OR is_reserved IS NULL)"
                )
            ).fetchone()

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
            tier1 = await (
                await db.execute(
                    f"SELECT COUNT(*) FROM items WHERE price < 10000 AND {active_filter}"
                )
            ).fetchone()
            tier2 = await (
                await db.execute(
                    f"SELECT COUNT(*) FROM items WHERE price >= 10000 AND price < 50000 AND {active_filter}"
                )
            ).fetchone()
            tier3 = await (
                await db.execute(
                    f"SELECT COUNT(*) FROM items WHERE price >= 50000 AND price < 100000 AND {active_filter}"
                )
            ).fetchone()
            tier4 = await (
                await db.execute(
                    f"SELECT COUNT(*) FROM items WHERE price >= 100000 AND {active_filter}"
                )
            ).fetchone()
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
            "price_distribution": price_distribution,
        }

    async def get_search_market_stats(
        self, search_query_id: Optional[int] = None
    ) -> Tuple[Optional[int], Optional[int]]:
        """Расчет медианной и средней рыночной стоимости товаров в конкретном поиске"""
        if search_query_id is None:
            return None, None

        where = "EXISTS (SELECT 1 FROM item_searches membership WHERE membership.item_id=items.id AND membership.search_id=?) AND price IS NOT NULL AND price > 0 AND COALESCE(is_hidden,0)=0 AND COALESCE(is_closed,0)=0"
        params = [search_query_id]

        async with self.connection() as db:
            cursor = await db.execute(
                f"SELECT COUNT(*), AVG(price) FROM items WHERE {where}", params
            )
            row = await cursor.fetchone()
            if not row or not row[0]:
                return None, None
            count, avg_p = row[0], int(row[1]) if row[1] else None

            cursor_med = await db.execute(
                f"SELECT price FROM items WHERE {where} ORDER BY price ASC LIMIT ? OFFSET ?",
                params + [2 if count % 2 == 0 else 1, (count - 1) // 2],
            )
            middle = await cursor_med.fetchall()
            med_p = (
                int(sum(row[0] for row in middle) / len(middle)) if middle else avg_p
            )
            return med_p, avg_p

    async def get_search_market_price(
        self, search_query_id: Optional[int]
    ) -> Optional[int]:
        """Расчет средней рыночной стоимости товаров в конкретном поиске"""
        _, avg_p = await self.get_search_market_stats(search_query_id)
        return avg_p

    async def get_price_stats(
        self, search_query_id: Optional[int] = None, query: Optional[str] = None
    ) -> PriceStats:
        """Расчет детальной ценовой статистики (мин, макс, средняя, медиана, оценка пула)"""
        where_clauses = [
            "price IS NOT NULL AND price > 0",
            "(is_hidden = 0 OR is_hidden IS NULL)",
            "(is_closed = 0 OR is_closed IS NULL)",
            "(is_reserved = 0 OR is_reserved IS NULL)",
        ]
        params: List[Any] = []
        if search_query_id is not None:
            if str(search_query_id) in ("-1", "unassigned", "null"):
                where_clauses.append("search_query_id IS NULL")
            else:
                where_clauses.append(
                    "EXISTS (SELECT 1 FROM item_searches membership WHERE membership.item_id=items.id AND membership.search_id=?)"
                )
                params.append(int(search_query_id))

        if query:
            where_clauses.append("(title LIKE ? OR description LIKE ?)")
            pattern = f"%{query.strip()}%"
            params.extend([pattern, pattern])

        where_sql = " AND ".join(where_clauses)

        async with self.connection() as db:
            cursor = await db.execute(
                f"""
                SELECT COUNT(*), MIN(price), MAX(price), AVG(price), SUM(price)
                FROM items WHERE {where_sql}
            """,
                params,
            )
            agg = await cursor.fetchone()

            if not agg or agg[0] == 0:
                return PriceStats()

            count, min_p, max_p, avg_p, sum_p = agg

            # Расчет медианной цены
            cursor_med = await db.execute(
                f"""
                SELECT price FROM items WHERE {where_sql} ORDER BY price ASC LIMIT ? OFFSET ?
            """,
                params + [2 if count % 2 == 0 else 1, (count - 1) // 2],
            )
            middle = await cursor_med.fetchall()
            median_p = (
                int(sum(row[0] for row in middle) / len(middle))
                if middle
                else int(avg_p)
            )

            return PriceStats(
                item_count=count,
                min_price=min_p,
                max_price=max_p,
                avg_price=int(avg_p) if avg_p else None,
                median_price=median_p,
                total_valuation=int(sum_p) if sum_p else 0,
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
                cur_upd = await db.execute(
                    """
                    UPDATE items SET
                        market_avg_price = ?,
                        is_hot_deal = CASE WHEN price <= ? THEN 1 ELSE 0 END
                    WHERE search_query_id = ?
                """,
                    (avg_int, hot_thresh, sq_id),
                )
                updated_count += cur_upd.rowcount

            await db.commit()
        return updated_count

    async def get_price_trend_history(self, days: int = 14) -> List[Dict[str, Any]]:
        """История средних цен и падений цен по дням для аналитического графика"""
        result: List[Dict[str, Any]] = []
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
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
            """,
                (days,),
            )
            rows = await cursor.fetchall()
            for r in rows:
                result.append(
                    {
                        "date": r["log_date"],
                        "changes_count": r["changes_count"],
                        "avg_price": int(r["avg_new_price"] or 0),
                        "drops_count": r["drops_count"],
                        "avg_discount": int(r["avg_discount"] or 0),
                    }
                )
        return result

    async def log_audit(
        self,
        action: str,
        username: str = "system",
        details: str = "",
        ip_address: str = "",
        user_id: Optional[int] = None,
    ) -> None:
        """Запись действия пользователя или системы в журнал безопасности"""
        try:
            async with self.connection() as db:
                await db.execute(
                    """
                    INSERT INTO audit_logs (user_id, username, action, details, ip_address, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (user_id, username, action, details, ip_address, datetime.now()),
                )
                await db.commit()
        except Exception:
            pass

    async def get_audit_logs(self, limit: int = 100) -> List[AuditLogEntry]:
        """Получение последних записей журнала аудита"""
        logs: List[AuditLogEntry] = []
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT id, user_id, username, action, details, ip_address, created_at
                FROM audit_logs ORDER BY id DESC LIMIT ?
            """,
                (limit,),
            )
            rows = await cursor.fetchall()
            for r in rows:
                logs.append(
                    AuditLogEntry(
                        id=r["id"],
                        user_id=r["user_id"],
                        username=r["username"],
                        action=r["action"],
                        details=r["details"],
                        ip_address=r["ip_address"],
                        created_at=datetime.fromisoformat(str(r["created_at"]))
                        if r["created_at"]
                        else datetime.now(),
                    )
                )
        return logs

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
            server_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

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
                    "max_hours": 0.0,
                }

            lifetimes = [r[3] for r in rows if r[3] is not None]
            count = len(lifetimes)
            if count == 0:
                return {
                    "count": 0,
                    "avg_hours": 0.0,
                    "median_hours": 0.0,
                    "min_hours": 0.0,
                    "max_hours": 0.0,
                }

            avg_h = sum(lifetimes) / count
            median_h = lifetimes[count // 2]
            min_h = min(lifetimes)
            max_h = max(lifetimes)

            return {
                "count": count,
                "avg_hours": round(avg_h, 1),
                "median_hours": round(median_h, 1),
                "min_hours": round(min_h, 1),
                "max_hours": round(max_h, 1),
            }
