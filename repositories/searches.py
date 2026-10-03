from datetime import datetime
from typing import Any, Dict, List, Optional
import aiosqlite
from models import SearchQuery


class SearchesRepository:
    async def add_search(self, search: SearchQuery) -> int:
        """Добавление нового поискового запроса для мониторинга"""
        async with self.connection() as db:
            cursor = await db.execute(
                """
                INSERT INTO searches (name, url, min_price, max_price, check_interval_min, enabled, active_hours_start, active_hours_end, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    search.name,
                    search.url,
                    search.min_price,
                    search.max_price,
                    search.check_interval_min,
                    search.enabled,
                    search.active_hours_start,
                    search.active_hours_end,
                    datetime.now(),
                ),
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def update_search(self, search: SearchQuery) -> bool:
        """Обновление параметров поискового запроса"""
        if not search.id:
            return False
        async with self.connection() as db:
            cursor = await db.execute(
                """
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
            """,
                (
                    search.name,
                    search.url,
                    search.min_price,
                    search.max_price,
                    search.check_interval_min,
                    search.enabled,
                    search.active_hours_start,
                    search.active_hours_end,
                    search.id,
                ),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def toggle_search_enabled(
        self, search_id: int, enabled: Optional[bool] = None
    ) -> bool:
        """Включение/выключение задачи мониторинга"""
        async with self.connection() as db:
            if enabled is None:
                cursor = await db.execute(
                    "UPDATE searches SET enabled = CASE WHEN enabled = 1 THEN 0 ELSE 1 END WHERE id = ?",
                    (search_id,),
                )
            else:
                cursor = await db.execute(
                    "UPDATE searches SET enabled = ? WHERE id = ?",
                    (1 if enabled else 0, search_id),
                )
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
                searches.append(
                    SearchQuery(
                        id=row["id"],
                        name=row["name"],
                        url=row["url"],
                        min_price=row["min_price"],
                        max_price=row["max_price"],
                        check_interval_min=row["check_interval_min"],
                        enabled=bool(row["enabled"]),
                        active_hours_start=row["active_hours_start"]
                        if "active_hours_start" in cols_s
                        else 0,
                        active_hours_end=row["active_hours_end"]
                        if "active_hours_end" in cols_s
                        else 24,
                        last_checked_at=datetime.fromisoformat(row["last_checked_at"])
                        if row["last_checked_at"]
                        else None,
                        created_at=datetime.fromisoformat(row["created_at"])
                        if row["created_at"]
                        else datetime.now(),
                    )
                )
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
                result.append(
                    {
                        "id": r["id"],
                        "name": r["name"],
                        "url": r["url"],
                        "min_price": r["min_price"],
                        "max_price": r["max_price"],
                        "check_interval_min": r["check_interval_min"],
                        "enabled": bool(r["enabled"]),
                        "active_hours_start": r["active_hours_start"]
                        if "active_hours_start" in cols_s
                        else 0,
                        "active_hours_end": r["active_hours_end"]
                        if "active_hours_end" in cols_s
                        else 24,
                        "last_checked_at": r["last_checked_at"],
                        "created_at": r["created_at"],
                        "items_count": r["items_count"],
                    }
                )
        return result

    async def get_search_by_id(self, search_id: int) -> Optional[SearchQuery]:
        """Получение запроса по ID"""
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM searches WHERE id = ?", (search_id,)
            )
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
                    active_hours_start=row["active_hours_start"]
                    if "active_hours_start" in cols_s
                    else 0,
                    active_hours_end=row["active_hours_end"]
                    if "active_hours_end" in cols_s
                    else 24,
                    last_checked_at=datetime.fromisoformat(row["last_checked_at"])
                    if row["last_checked_at"]
                    else None,
                    created_at=datetime.fromisoformat(row["created_at"])
                    if row["created_at"]
                    else datetime.now(),
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
            await db.execute(
                "UPDATE searches SET last_checked_at = ? WHERE id = ?",
                (datetime.now(), search_id),
            )
            await db.commit()
