"""Shared monitoring orchestration, independent of the HTTP application."""
import asyncio
import html
import json
import logging
import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
import httpx
from config import config
from database import db
from models import AvitoItem, SearchQuery
from browser_engine import browser_engine
from http_engine import http_engine
from coordination import lease
from outbox import outbox_worker
logger = logging.getLogger("AvitoMonitoring")

class MonitoringService:
    def __init__(self):
        self._run_lock = asyncio.Lock()
        self._search_locks = {}
        self.is_running: bool = False
        self.task: Optional[asyncio.Task] = None
        self.last_run: Optional[datetime] = None
        self.next_run: Optional[datetime] = None
        self.current_checking: Optional[str] = None
        self.last_results: Dict[str, Any] = {"new": 0, "drops": 0, "errors": 0}

    async def start(self):
        if self.is_running:
            return
        self.is_running = True
        self.task = asyncio.create_task(self._loop())
        logger.info("🟢 Фоновый сервис мониторинга Авито запущен")

    async def stop(self):
        if not self.is_running:
            return
        self.is_running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        self.current_checking = None
        logger.info("🔴 Фоновый сервис мониторинга Авито остановлен")

    async def check_search(self, search):
        lock = self._search_locks.setdefault(search.id, asyncio.Lock())
        if lock.locked():
            return {"new": 0, "drops": 0, "skipped": 1}
        async with lock:
            async with lease(f"search:{search.id}") as acquired:
                if not acquired:
                    return {"new": 0, "drops": 0, "skipped": 1}
                return await asyncio.wait_for(self._check_search(search), timeout=180)

    async def _check_search(self, search: SearchQuery) -> Dict[str, int]:
        """Проверка одного поискового запроса"""
        # Проверка активных часов поиска
        now_hour = datetime.now().hour
        start = getattr(search, 'active_hours_start', 0)
        end = getattr(search, 'active_hours_end', 24)
        if start < end:
            if now_hour < start or now_hour >= end:
                logger.info(f"⏸ Поиск '{search.name}' вне активных часов ({start}:00-{end}:00), пропуск")
                return {'new': 0, 'drops': 0}
        else:  # ночное расписание, например 22-8
            if end <= now_hour < start:
                logger.info(f"⏸ Поиск '{search.name}' вне активных часов ({start}:00-{end}:00), пропуск")
                return {'new': 0, 'drops': 0}

        self.current_checking = search.name
        logger.info(f"🔎 Мониторинг: проверка поиска #{search.id} '{search.name}'...")
        new_count = 0
        drop_count = 0

        try:
            # Сначала пробуем быстрый HTTP-парсер, при неудаче — браузерный
            result = None
            engine_used = "http"
            try:
                result = await http_engine.parse_search(search.url, max_pages=1)
            except Exception as e:
                logger.debug(f"HTTP-парсер не смог обработать '{search.name}': {e}")
                result = None

            if result and result.outcome == "blocked":
                return {"new": 0, "drops": 0, "errors": 1}
            if not result or not result.items:
                engine_used = "browser"
                result = await browser_engine.parse_search(search.url, max_pages=1)

            logger.info(f"⚙️ Поиск '{search.name}': использован {engine_used}-движок")

            if result.items:
                # Привязываем search_query_id
                for it in result.items:
                    it.search_query_id = search.id

                save_res = await db.save_items(result.items)
                new_count = save_res["new_count"]
                drop_count = save_res["price_drop_count"]

                # Автоматическая синхронизация: помечаем отсутствующие лоты как закрытые/проданные
                active_ids = [it.id for it in result.items]
                closed_cnt = await db.cleanup_stale_items(search.id, active_ids) if result.exhaustive and not result.errors else 0
                if closed_cnt > 0:
                    logger.info(f"🧹 Поиск '{search.name}': скрыто {closed_cnt} проданных/исчезнувших лотов")

                # Логирование скорости рынка для выгодных лотов
                try:
                    lifetime_stats = await db.get_gem_lifetime_stats()
                    if lifetime_stats and lifetime_stats.get('count', 0) > 0:
                        avg_h = lifetime_stats.get('avg_hours', 0)
                        logger.info(f"📊 Скорость рынка: выгодные лоты живут в среднем {avg_h:.1f}ч (выборка: {lifetime_stats['count']})")
                except Exception:
                    pass

                await outbox_worker.drain()

            await db.update_search_last_checked(search.id)
        except Exception as e:
            logger.error(f"Ошибка при проверке поиска #{search.id}: {type(e).__name__}")
            return {"new": new_count, "drops": drop_count, "errors": 1}
        finally:
            self.current_checking = None

        return {"new": new_count, "drops": drop_count}

    async def run_all_now(self, due_only=False):
        if self._run_lock.locked():
            return {"new": 0, "drops": 0, "errors": 0, "skipped": 1}
        async with self._run_lock:
            async with lease("monitoring-cycle") as acquired:
                if not acquired:
                    return {"new": 0, "drops": 0, "errors": 0, "skipped": 1}
                return await self._run_all_now(due_only=due_only)

    async def _run_all_now(self, due_only=False) -> Dict[str, int]:
        """Принудительная проверка всех включенных поисков (параллельно, макс. 3 одновременно)"""
        searches = await db.get_searches(enabled_only=True)
        if due_only:
            now = datetime.now()
            searches = [s for s in searches if s.last_checked_at is None or
                        now - s.last_checked_at >= timedelta(minutes=max(1, s.check_interval_min))]
        total_new = 0
        total_drops = 0
        self.last_run = datetime.now()

        sem = asyncio.Semaphore(3)  # макс. 3 параллельных проверки

        async def check_with_sem(s):
            async with sem:
                return await self.check_search(s)

        tasks = [check_with_sem(s) for s in searches]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            if isinstance(res, dict):
                total_new += res.get('new', 0)
                total_drops += res.get('drops', 0)

        self.last_results = {'new': total_new, 'drops': total_drops, 'errors': sum(1 if isinstance(r, Exception) else r.get('errors', 0) for r in results)}
        interval = config.telegram.notification_interval_min
        self.next_run = datetime.now() + timedelta(minutes=interval)
        return self.last_results

    async def _loop(self):
        while self.is_running:
            try:
                await self.run_all_now(due_only=True)
            except Exception as e:
                logger.error(f"Ошибка в цикле мониторинга: {e}")
            
            interval_sec = 30
            self.next_run = datetime.now() + timedelta(seconds=interval_sec)
            try:
                await asyncio.sleep(interval_sec)
            except asyncio.CancelledError:
                break

monitor_service = MonitoringService()

