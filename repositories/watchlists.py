"""Per-user watchlists and confirmed Telegram destinations."""

import hashlib
import json
import secrets
import time
from typing import Any, Dict, List, Optional

import aiosqlite


class WatchlistsRepository:
    async def default_watchlist_id(self, user_id: int) -> int:
        async with self.connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            await connection.execute(
                "INSERT OR IGNORE INTO watchlists(user_id,name,is_default,created_at) VALUES (?,'Избранное',1,?)",
                (user_id, time.time()),
            )
            row = await (await connection.execute(
                "SELECT id FROM watchlists WHERE user_id=? AND is_default=1", (user_id,),
            )).fetchone()
            await connection.commit()
            return row[0]

    async def list_watchlists(self, user_id: int) -> List[Dict[str, Any]]:
        await self.default_watchlist_id(user_id)
        async with self.connection() as connection:
            connection.row_factory = aiosqlite.Row
            rows = await (await connection.execute(
                """SELECT w.*,COUNT(wi.item_id) AS item_count FROM watchlists w
                   LEFT JOIN watchlist_items wi ON wi.watchlist_id=w.id
                   WHERE w.user_id=? GROUP BY w.id ORDER BY w.is_default DESC,w.id""",
                (user_id,),
            )).fetchall()
            return [dict(row) for row in rows]

    async def create_watchlist(self, user_id: int, name: str) -> int:
        async with self.connection() as connection:
            cursor = await connection.execute(
                "INSERT INTO watchlists(user_id,name,created_at) VALUES (?,?,?)",
                (user_id, name, time.time()),
            )
            await connection.commit()
            return cursor.lastrowid

    async def update_watchlist(self, user_id: int, watchlist_id: int, *, name: Optional[str] = None, alerts_paused: Optional[bool] = None) -> bool:
        parts, params = [], []
        if name is not None:
            parts.append("name=?")
            params.append(name)
        if alerts_paused is not None:
            parts.append("alerts_paused=?")
            params.append(int(alerts_paused))
        if not parts:
            return False
        async with self.connection() as connection:
            cursor = await connection.execute(
                f"UPDATE watchlists SET {','.join(parts)} WHERE id=? AND user_id=?",
                (*params, watchlist_id, user_id),
            )
            await connection.commit()
            return cursor.rowcount > 0

    async def delete_watchlist(self, user_id: int, watchlist_id: int) -> bool:
        async with self.connection() as connection:
            cursor = await connection.execute(
                "DELETE FROM watchlists WHERE id=? AND user_id=? AND is_default=0",
                (watchlist_id, user_id),
            )
            await connection.commit()
            return cursor.rowcount > 0

    async def list_watchlist_items(self, user_id: int, watchlist_id: int) -> Optional[List[Dict[str, Any]]]:
        async with self.connection() as connection:
            connection.row_factory = aiosqlite.Row
            exists = await (await connection.execute(
                "SELECT 1 FROM watchlists WHERE id=? AND user_id=?", (watchlist_id, user_id),
            )).fetchone()
            if not exists:
                return None
            rows = await (await connection.execute(
                """SELECT wi.item_id,wi.target_price,wi.note,wi.alerts_paused,
                          i.title,i.price,i.url,i.main_image FROM watchlist_items wi
                   JOIN items i ON i.id=wi.item_id WHERE wi.watchlist_id=?
                   ORDER BY wi.created_at DESC""", (watchlist_id,),
            )).fetchall()
            return [dict(row) for row in rows]

    async def set_watchlist_item(self, user_id: int, watchlist_id: int, item_id: str, *, target_price: Optional[int] = None, note: str = "", alerts_paused: bool = False) -> bool:
        async with self.connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            owner = await (await connection.execute(
                "SELECT 1 FROM watchlists WHERE id=? AND user_id=?", (watchlist_id, user_id),
            )).fetchone()
            item = await (await connection.execute("SELECT 1 FROM items WHERE id=?", (item_id,))).fetchone()
            if not owner or not item:
                return False
            await connection.execute(
                """INSERT INTO watchlist_items(watchlist_id,item_id,target_price,note,alerts_paused,created_at)
                   VALUES (?,?,?,?,?,?) ON CONFLICT(watchlist_id,item_id) DO UPDATE SET
                   target_price=excluded.target_price,note=excluded.note,alerts_paused=excluded.alerts_paused""",
                (watchlist_id, item_id, target_price, note, int(alerts_paused), time.time()),
            )
            await connection.commit()
            return True

    async def remove_watchlist_item(self, user_id: int, watchlist_id: int, item_id: str) -> bool:
        async with self.connection() as connection:
            cursor = await connection.execute(
                "DELETE FROM watchlist_items WHERE item_id=? AND watchlist_id IN (SELECT id FROM watchlists WHERE id=? AND user_id=?)",
                (item_id, watchlist_id, user_id),
            )
            await connection.commit()
            return cursor.rowcount > 0

    async def toggle_personal_favorite(self, user_id: int, item_id: str) -> bool:
        watchlist_id = await self.default_watchlist_id(user_id)
        async with self.connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            exists = await (await connection.execute(
                "SELECT 1 FROM watchlist_items WHERE watchlist_id=? AND item_id=?", (watchlist_id, item_id),
            )).fetchone()
            if exists:
                await connection.execute("DELETE FROM watchlist_items WHERE watchlist_id=? AND item_id=?", (watchlist_id, item_id))
            else:
                await connection.execute(
                    "INSERT OR IGNORE INTO watchlist_items(watchlist_id,item_id,created_at) VALUES (?,?,?)",
                    (watchlist_id, item_id, time.time()),
                )
            await connection.commit()
            return not bool(exists)

    async def personal_favorite_ids(self, user_id: int, item_ids: List[str]) -> set[str]:
        if not item_ids:
            return set()
        placeholders = ",".join("?" for _ in item_ids)
        async with self.connection() as connection:
            rows = await (await connection.execute(
                "SELECT wi.item_id FROM watchlist_items wi JOIN watchlists w ON w.id=wi.watchlist_id "
                f"WHERE w.user_id=? AND w.is_default=1 AND wi.item_id IN ({placeholders})",
                (user_id, *item_ids),
            )).fetchall()
            return {row[0] for row in rows}

    async def set_personal_favorites(self, user_id: int, item_ids: List[str], enabled: bool) -> int:
        if not item_ids:
            return 0
        watchlist_id = await self.default_watchlist_id(user_id)
        async with self.connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            if enabled:
                cursor = await connection.executemany(
                    "INSERT OR IGNORE INTO watchlist_items(watchlist_id,item_id,created_at) SELECT ?,id,? FROM items WHERE id=?",
                    [(watchlist_id, time.time(), item_id) for item_id in set(item_ids)],
                )
            else:
                cursor = await connection.executemany(
                    "DELETE FROM watchlist_items WHERE watchlist_id=? AND item_id=?",
                    [(watchlist_id, item_id) for item_id in set(item_ids)],
                )
            await connection.commit()
            return cursor.rowcount

    async def issue_telegram_link(self, user_id: int) -> str:
        code = secrets.token_urlsafe(16)
        digest = hashlib.sha256(code.encode()).hexdigest()
        async with self.connection() as connection:
            await connection.execute("DELETE FROM telegram_link_challenges WHERE user_id=? OR expires_at<?", (user_id, time.time()))
            await connection.execute("INSERT INTO telegram_link_challenges(code_hash,user_id,expires_at) VALUES (?,?,?)", (digest, user_id, time.time() + 600))
            await connection.commit()
        return code

    async def redeem_telegram_link(self, code: str, chat_id: int) -> bool:
        digest = hashlib.sha256(code.encode()).hexdigest()
        async with self.connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            row = await (await connection.execute(
                "SELECT user_id FROM telegram_link_challenges WHERE code_hash=? AND expires_at>?",
                (digest, time.time()),
            )).fetchone()
            if not row:
                return False
            existing = await (await connection.execute(
                "SELECT user_id FROM user_telegram_chats WHERE chat_id=?", (chat_id,),
            )).fetchone()
            if existing and existing[0] != row[0]:
                return False
            await connection.execute("INSERT OR IGNORE INTO user_telegram_chats(user_id,chat_id,created_at) VALUES (?,?,?)", (row[0], chat_id, time.time()))
            await connection.execute("DELETE FROM telegram_link_challenges WHERE code_hash=?", (digest,))
            await connection.commit()
            return True

    async def list_telegram_chats(self, user_id: int) -> List[int]:
        async with self.connection() as connection:
            rows = await (await connection.execute("SELECT chat_id FROM user_telegram_chats WHERE user_id=?", (user_id,))).fetchall()
            return [row[0] for row in rows]

    async def unlink_telegram_chat(self, user_id: int, chat_id: int) -> bool:
        async with self.connection() as connection:
            cursor = await connection.execute("DELETE FROM user_telegram_chats WHERE user_id=? AND chat_id=?", (user_id, chat_id))
            await connection.commit()
            return cursor.rowcount > 0

    async def enqueue_watchlist_alerts(self, connection, item, price: int, payload: dict, timestamp: float) -> None:
        rows = await (await connection.execute(
            """SELECT w.id,w.name,wi.target_price,c.chat_id FROM watchlist_items wi
               JOIN watchlists w ON w.id=wi.watchlist_id
               JOIN users u ON u.id=w.user_id AND u.is_active=1
               JOIN user_telegram_chats c ON c.user_id=w.user_id
               WHERE wi.item_id=? AND wi.target_price>=? AND wi.alerts_paused=0 AND w.alerts_paused=0""",
            (item.id, price),
        )).fetchall()
        for watchlist_id, name, target, chat_id in rows:
            alert = dict(payload)
            alert["type"] = "price_drop"
            alert["search_name"] = f"{name}: цель {target} ₽"
            alert["watch_target_price"] = target
            event_key = f"watch:{item.id}:{price}:{chat_id}"
            await connection.execute(
                "INSERT OR IGNORE INTO notification_outbox(event_key,channel,destination,payload,created_at) VALUES (?,'telegram',?,?,?)",
                (event_key, str(chat_id), json.dumps(alert, ensure_ascii=False), timestamp),
            )
