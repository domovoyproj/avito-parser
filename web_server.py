import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import hashlib
import hmac
import html
import json
import logging
import os
from pathlib import Path
import random
import re
import sys
import time
from typing import Any, Dict, List, Optional
import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, model_validator

from browser_engine import browser_engine
from config import ScraperConfig, config
from database import db
from exporter import exporter
from http_engine import http_engine
from models import (
    AISettings, AvitoItem, ChangePasswordRequest, ExportFormat, LoginRequest,
    SearchQuery, SellerInfo, User, UserCreate, UserRole, UserUpdate
)
from ai_scoring import deal_scoring_engine
from parser_core import AvitoDataExtractor
from proxy_manager import ProxyManager, proxy_manager

# --- Логирование и кольцевой буфер для Веб-панели ---
class WebLogBuffer(logging.Handler):
    def __init__(self, max_records: int = 300):
        super().__init__()
        self.max_records = max_records
        self.records: List[Dict[str, Any]] = []

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            entry = {
                "id": len(self.records) + 1,
                "timestamp": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
                "level": record.levelname,
                "name": record.name,
                "message": msg,
            }
            self.records.append(entry)
            if len(self.records) > self.max_records:
                self.records.pop(0)
        except Exception:
            pass

    def clear(self):
        self.records.clear()

log_buffer = WebLogBuffer()
formatter = logging.Formatter("%(message)s")
log_buffer.setFormatter(formatter)
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(log_buffer)

logger = logging.getLogger("AvitoWeb")

# --- Сервис фонового мониторинга ---
class MonitoringService:
    def __init__(self):
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

    async def _send_telegram_notification(self, item: AvitoItem, search_name: str, notif_type: str):
        """Отправка уведомления в Telegram через HTTP API Bot"""
        if not config.telegram.bot_token or not config.telegram.admin_chat_ids:
            return

        header = "🆕 <b>НОВОЕ ОБЪЯВЛЕНИЕ</b>" if notif_type == "new" else "📉 <b>ЦЕНА СНИЗИЛАСЬ!</b>"
        
        score_val = item.deal_score if item.deal_score is not None else 50
        if score_val >= 85:
            grade_badge = f"💎 <b>GEM-ЛОТ (Score: {score_val}/100)</b>"
        elif score_val >= 70:
            grade_badge = f"🔥 <b>ВЫГОДНАЯ СДЕЛКА (Score: {score_val}/100)</b>"
        elif score_val >= 50:
            grade_badge = f"⚖️ <b>FAIR (Score: {score_val}/100)</b>"
        else:
            grade_badge = f"⚠️ <b>Внимание/Риск (Score: {score_val}/100)</b>"

        market_info_line = ""
        if item.price and item.search_query_id:
            market_avg = await db.get_search_market_price(item.search_query_id)
            if market_avg and market_avg > item.price:
                savings_rub = market_avg - item.price
                pct_below = round((savings_rub / market_avg) * 100)
                if pct_below >= 10:
                    market_info_line = f"\n📊 <b>Средняя цена:</b> {market_avg:,} ₽ <i>(выгода {savings_rub:,} ₽ / -{pct_below}%)</i>".replace(",", " ")

        price_str = f"<b>{item.price:,} ₽</b>".replace(",", " ") if item.price else "Цена не указана"
        if notif_type == "price_drop" and item.old_price and item.price:
            old_str = f"{item.old_price:,} ₽".replace(",", " ")
            delta = item.old_price - item.price
            price_str += f" <i>(было {old_str}, скидка {delta:,} ₽)</i>".replace(",", " ")

        esc_title = html.escape(item.title or "Без названия")
        esc_search = html.escape(search_name or "Поиск")
        esc_address = html.escape(item.address or "Не указана")
        esc_seller = f"\n👤 <b>Продавец:</b> {html.escape(item.seller.name)}" if item.seller and item.seller.name else ""
        delivery_badge = " | 🚚 Авито Доставка" if item.delivery_available else ""

        reasons_block = ""
        if item.deal_reasons:
            reasons_block = "\n" + "\n".join([f"  ✅ {html.escape(r)}" for r in item.deal_reasons[:3]])

        flaws_block = ""
        if item.detected_flaws:
            flaws_block = "\n" + "\n".join([f"  ⚠️ <b>Внимание:</b> {html.escape(f)}" for f in item.detected_flaws[:2]])

        ai_summary_block = ""
        if item.ai_summary:
            ai_summary_block = f"\n\n🤖 <b>AI-Вердикт:</b> <i>{html.escape(item.ai_summary)}</i>"

        text = (
            f"{header}\n"
            f"{grade_badge}\n"
            f"🎯 <b>Поиск:</b> {esc_search}\n\n"
            f"📦 <b>{esc_title}</b>\n"
            f"💰 <b>Цена:</b> {price_str}{market_info_line}\n"
            f"📍 <b>Локация:</b> {esc_address}{delivery_badge}{esc_seller}"
            f"{reasons_block}"
            f"{flaws_block}"
            f"{ai_summary_block}\n\n"
            f"🔗 <a href='{item.url}'>Открыть объявление на Авито</a>"
        )

        reply_markup = {
            "inline_keyboard": [
                [{"text": "↗️ На Авито", "url": item.url}],
                [
                    {"text": "⭐", "callback_data": f"tg_fav_{item.id}"},
                    {"text": "🙈", "callback_data": f"tg_hide_{item.id}"},
                    {"text": "🤖 AI", "callback_data": f"tg_ai_{item.id}"}
                ]
            ]
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            for chat_id in config.telegram.admin_chat_ids:
                try:
                    settings = await db.get_chat_settings(chat_id)
                    if notif_type == "new" and not settings.notify_new:
                        continue
                    if notif_type == "price_drop" and not settings.notify_drops:
                        continue
                    if settings.delivery_only and not item.delivery_available:
                        continue
                    if settings.min_deal_score > 0 and (item.deal_score or 0) < settings.min_deal_score:
                        continue

                    # Проверка фильтра "Только дешевле рынка"
                    if settings.only_below_market and item.price and item.search_query_id:
                        market_avg = await db.get_search_market_price(item.search_query_id)
                        if market_avg and market_avg > 0:
                            threshold = settings.below_market_pct / 100.0
                            max_allowed = int(market_avg * (1.0 - threshold))
                            if item.price > max_allowed:
                                continue

                    if notif_type == "price_drop" and settings.min_discount_pct > 0 and item.old_price and item.price:
                        discount_pct = round(((item.old_price - item.price) / item.old_price) * 100)
                        if discount_pct < settings.min_discount_pct:
                            continue
                    if settings.send_photos and item.main_image:
                        url = f"https://api.telegram.org/bot{config.telegram.bot_token}/sendPhoto"
                        payload = {
                            "chat_id": chat_id,
                            "photo": item.main_image,
                            "caption": text,
                            "parse_mode": "HTML",
                            "reply_markup": json.dumps(reply_markup)
                        }
                    else:
                        url = f"https://api.telegram.org/bot{config.telegram.bot_token}/sendMessage"
                        payload = {
                            "chat_id": chat_id,
                            "text": text,
                            "parse_mode": "HTML",
                            "reply_markup": json.dumps(reply_markup)
                        }
                    await client.post(url, data=payload)
                    await asyncio.sleep(0.3)
                except Exception as e:
                    logger.error(f"Ошибка отправки в Telegram для chat_id={chat_id}: {e}")
    async def _send_webhook_notification(self, item: AvitoItem, search_name: str, notif_type: str):
        """Отправка уведомления через Webhook (JSON POST)"""
        try:
            settings = await db.get_webhook_settings()
        except Exception:
            return
        if not settings.enabled or not settings.url:
            return
        if settings.min_deal_score > 0 and (item.deal_score or 0) < settings.min_deal_score:
            return
        if notif_type == "new" and not settings.send_new:
            return
        if notif_type == "price_drop" and not settings.send_drops:
            return

        payload = {
            'event': notif_type,
            'item': {
                'id': item.id,
                'title': item.title,
                'price': item.price,
                'old_price': item.old_price,
                'url': item.url,
                'deal_score': item.deal_score,
                'deal_grade': item.deal_grade,
                'deal_reasons': item.deal_reasons,
                'detected_flaws': item.detected_flaws,
                'address': item.address,
                'delivery_available': item.delivery_available,
                'main_image': item.main_image,
            },
            'search_name': search_name,
            'timestamp': datetime.now().isoformat()
        }

        headers = {"Content-Type": "application/json"}
        body_bytes = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        if settings.secret:
            sig = hmac.new(settings.secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
            headers["X-Webhook-Signature"] = sig

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(settings.url, content=body_bytes, headers=headers)
                if resp.status_code >= 400:
                    logger.warning(f"⚠️ Webhook вернул HTTP {resp.status_code} для {notif_type} ({item.id})")
        except Exception as e:
            logger.error(f"Ошибка отправки Webhook ({notif_type}, {item.id}): {e}")

    async def check_search(self, search: SearchQuery) -> Dict[str, int]:
        """Проверка одного поискового запроса"""
        # Проверка активных часов поиска
        now_hour = datetime.now().hour
        start = getattr(search, 'active_hours_start', 0)
        end = getattr(search, 'active_hours_end', 24)
        if end == 24:
            pass  # 24/7 мониторинг
        elif start < end:
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
                closed_cnt = await db.cleanup_stale_items(search.id, active_ids)
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

                # Отправка уведомлений (Telegram + Webhook)
                for new_item in save_res["new_items"]:
                    if not await db.is_notification_sent(new_item.id, search.id, "new"):
                        await self._send_telegram_notification(new_item, search.name, "new")
                        await self._send_webhook_notification(new_item, search.name, "new")
                        await db.mark_notification_sent(new_item.id, search.id, "new")

                for change in save_res["price_changes"]:
                    if not await db.is_notification_sent(change.item_id, search.id, "price_drop"):
                        item_obj = await db.get_item_by_id(change.item_id)
                        if item_obj:
                            await self._send_telegram_notification(item_obj, search.name, "price_drop")
                            await self._send_webhook_notification(item_obj, search.name, "price_drop")
                            await db.mark_notification_sent(change.item_id, search.id, "price_drop")

                logger.info(f"✓ Поиск '{search.name}': найдено {len(result.items)} объявлений (новых: {new_count}, скидок: {drop_count})")

            await db.update_search_last_checked(search.id)
        except Exception as e:
            logger.error(f"Ошибка при проверке поиска '{search.name}': {e}")
        finally:
            self.current_checking = None

        return {"new": new_count, "drops": drop_count}

    async def run_all_now(self) -> Dict[str, int]:
        """Принудительная проверка всех включенных поисков (параллельно, макс. 3 одновременно)"""
        searches = await db.get_searches(enabled_only=True)
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

        self.last_results = {'new': total_new, 'drops': total_drops, 'errors': sum(1 for r in results if isinstance(r, Exception))}
        interval = config.telegram.notification_interval_min
        self.next_run = datetime.now() + timedelta(minutes=interval)
        return self.last_results

    async def _loop(self):
        while self.is_running:
            try:
                await self.run_all_now()
            except Exception as e:
                logger.error(f"Ошибка в цикле мониторинга: {e}")
            
            interval_sec = config.telegram.notification_interval_min * 60
            self.next_run = datetime.now() + timedelta(seconds=interval_sec)
            try:
                await asyncio.sleep(interval_sec)
            except asyncio.CancelledError:
                break

monitor_service = MonitoringService()

# --- Менеджер активных парсинг-сессий (WebSockets) ---
class ParsingJobManager:
    def __init__(self):
        self.active_jobs: Dict[str, Dict[str, Any]] = {}
        self.subscribers: Dict[str, List[WebSocket]] = {}

    def create_job(self, job_id: str, url: str, max_pages: int, engine: str) -> Dict[str, Any]:
        job = {
            "job_id": job_id,
            "url": url,
            "max_pages": max_pages,
            "engine": engine,
            "status": "pending",
            "progress_pct": 0,
            "current_page": 0,
            "items_found": 0,
            "logs": [],
            "items": [],
            "errors": [],
            "start_time": time.time(),
            "elapsed_sec": 0
        }
        self.active_jobs[job_id] = job
        self.subscribers[job_id] = []
        return job

    async def broadcast(self, job_id: str, message_type: str, data: Any):
        if job_id in self.active_jobs:
            if message_type == "log":
                self.active_jobs[job_id]["logs"].append(data)
            elif message_type == "progress":
                self.active_jobs[job_id].update(data)
            elif message_type == "complete":
                self.active_jobs[job_id]["status"] = "completed"
                self.active_jobs[job_id].update(data)
            elif message_type == "error":
                self.active_jobs[job_id]["status"] = "error"
                self.active_jobs[job_id]["errors"].append(data)

        msg = json.dumps({"type": message_type, "data": data, "job_id": job_id})
        for ws in self.subscribers.get(job_id, []):
            try:
                await ws.send_text(msg)
            except Exception:
                pass

job_manager = ParsingJobManager()

# --- Жизненный цикл FastAPI ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Старт
    await db.init_db()
    logger.info("🚀 База данных Avito SQLite инициализирована")
    # Загрузка пользовательских правил скоринга в AI-движок
    try:
        rules = await db.get_custom_scoring_rules(active_only=True)
        deal_scoring_engine.load_custom_rules(rules)
        if rules:
            logger.info(f"📐 Загружено {len(rules)} пользовательских правил скоринга")
    except Exception as e:
        logger.warning(f"Не удалось загрузить правила скоринга: {e}")
    # Автозапуск мониторинга, если есть активные поиски
    searches = await db.get_searches(enabled_only=True)
    if searches:
        await monitor_service.start()
    yield
    # Остановка
    await monitor_service.stop()
    logger.info("Веб-панель Avito Parser остановлена")

app = FastAPI(
    title="Avito Parser Pro Web Panel",
    description="Современная веб-панель управления парсером, базой объявлений и мониторингом Авито",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Монтирование статики и шаблонов
STATIC_DIR = config.base_dir / "static"
TEMPLATES_DIR = config.base_dir / "templates"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ==============================================================================
# АВТОРИЗАЦИЯ И СЕССИИ
# ==============================================================================

SESSION_COOKIE_NAME = "avito_session"

async def get_optional_user(request: Request) -> Optional[User]:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        auth_hdr = request.headers.get("Authorization")
        if auth_hdr and auth_hdr.startswith("Bearer "):
            token = auth_hdr[7:].strip()
    if not token:
        return None
    return await db.get_user_by_session(token)

async def require_auth(request: Request) -> User:
    user = await get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    return user


@app.middleware("http")
async def protect_api(request: Request, call_next):
    """Apply session authentication to the complete API, including exports."""
    if request.url.path.startswith("/api/") and request.url.path != "/api/auth/login":
        if not await get_optional_user(request):
            return JSONResponse(status_code=401, content={"detail": "Требуется авторизация"})
    return await call_next(request)

async def require_admin(user: User = Depends(require_auth)) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Доступ разрешен только администраторам")
    return user

def render_auth_page(page_name: str, admin_only: bool = False):
    async def _handler(request: Request):
        user = await get_optional_user(request)
        if not user:
            return RedirectResponse(url="/login", status_code=303)
        if admin_only and user.role != UserRole.ADMIN:
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name=f"{page_name}.html",
            context={"page": page_name, "user": user.model_dump(mode="json")}
        )
    return _handler

# ==============================================================================
# HTML СТРАНИЦЫ И МАРШРУТЫ
# ==============================================================================

@app.get("/login", response_class=HTMLResponse)
async def page_login(request: Request):
    user = await get_optional_user(request)
    if user:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html", context={"page": "login"})

@app.get("/", response_class=HTMLResponse)
async def page_dashboard(request: Request):
    return await render_auth_page("dashboard")(request)

@app.get("/parser", response_class=HTMLResponse)
async def page_parser(request: Request):
    return await render_auth_page("parser")(request)

@app.get("/item-parser", response_class=HTMLResponse)
async def page_item_parser(request: Request):
    return await render_auth_page("item_parser")(request)

@app.get("/monitoring", response_class=HTMLResponse)
async def page_monitoring(request: Request):
    return await render_auth_page("monitoring")(request)

@app.get("/items", response_class=HTMLResponse)
async def page_items(request: Request):
    return await render_auth_page("items")(request)

@app.get("/price-drops", response_class=HTMLResponse)
async def page_price_drops(request: Request):
    return await render_auth_page("price_drops")(request)

@app.get("/proxies", response_class=HTMLResponse)
async def page_proxies(request: Request):
    return await render_auth_page("proxies", admin_only=True)(request)

@app.get("/settings", response_class=HTMLResponse)
async def page_settings(request: Request):
    return await render_auth_page("settings", admin_only=True)(request)

@app.get("/logs", response_class=HTMLResponse)
async def page_logs(request: Request):
    return await render_auth_page("logs")(request)

@app.get("/admin/users", response_class=HTMLResponse)
async def page_admin_users(request: Request):
    return await render_auth_page("admin_users", admin_only=True)(request)

# ==============================================================================
# REST API: АВТОРИЗАЦИЯ И ПОЛЬЗОВАТЕЛИ
# ==============================================================================

@app.post("/api/auth/login")
async def api_auth_login(req: LoginRequest, response: Response):
    user = await db.authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Неверное имя пользователя или пароль")
    token = await db.create_session(user.id, days_valid=14)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=14 * 86400,
        httponly=True,
        samesite="lax",
        path="/"
    )
    logger.info(f"🔑 Успешный вход пользователя '{user.username}' (роль: {user.role.value})")
    return {
        "status": "success",
        "user": user.model_dump(mode="json"),
        "token": token
    }

@app.post("/api/auth/logout")
async def api_auth_logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        await db.delete_session(token)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"status": "success"}

@app.get("/api/auth/me")
async def api_auth_me(user: User = Depends(require_auth)):
    return {"user": user.model_dump(mode="json")}

@app.post("/api/auth/change-password")
async def api_auth_change_password(req: ChangePasswordRequest, user: User = Depends(require_auth)):
    ok, msg = await db.change_password(user.id, req.old_password, req.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "success", "message": msg}

# ==============================================================================
# REST API: АДМИНИСТРИРОВАНИЕ ПОЛЬЗОВАТЕЛЕЙ
# ==============================================================================

@app.get("/api/admin/users")
async def api_admin_list_users(admin: User = Depends(require_admin)):
    users = await db.get_all_users()
    return {"users": [u.model_dump(mode="json") for u in users]}

@app.post("/api/admin/users")
async def api_admin_create_user(req: UserCreate, admin: User = Depends(require_admin)):
    ok, msg, user_id = await db.create_user(req.username, req.password, req.role)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    logger.info(f"👤 Админ '{admin.username}' создал пользователя '{req.username}' (роль: {req.role.value})")
    return {"status": "success", "message": msg, "user_id": user_id}

@app.put("/api/admin/users/{user_id}")
async def api_admin_update_user(user_id: int, req: UserUpdate, admin: User = Depends(require_admin)):
    ok, msg = await db.update_user(
        user_id=user_id,
        username=req.username,
        role=req.role,
        is_active=req.is_active,
        new_password=req.password
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    logger.info(f"👤 Админ '{admin.username}' обновил пользователя ID {user_id}")
    return {"status": "success", "message": msg}

@app.delete("/api/admin/users/{user_id}")
async def api_admin_delete_user(user_id: int, admin: User = Depends(require_admin)):
    ok, msg = await db.delete_user(user_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    logger.info(f"👤 Админ '{admin.username}' удалил пользователя ID {user_id}")
    return {"status": "success", "message": msg}
# REST API: СТАТИСТИКА И ДАШБОРД
# ==============================================================================

@app.get("/api/dashboard")
async def api_dashboard():
    data = await db.get_dashboard_summary()
    data["monitoring"] = {
        "is_running": monitor_service.is_running,
        "last_run": monitor_service.last_run.isoformat() if monitor_service.last_run else None,
        "next_run": monitor_service.next_run.isoformat() if monitor_service.next_run else None,
        "current_checking": monitor_service.current_checking,
        "last_results": monitor_service.last_results,
    }
    data["proxy_count"] = proxy_manager.total_count
    data["telegram_configured"] = bool(config.telegram.bot_token)
    try:
        data["lifetime_stats"] = await db.get_gem_lifetime_stats()
    except Exception:
        data["lifetime_stats"] = {"count": 0, "avg_hours": 0.0, "median_hours": 0.0, "min_hours": 0.0, "max_hours": 0.0}
    return data

@app.get("/api/stats")
async def api_stats():
    return await db.get_stats()



# ==============================================================================
# REST API: АНАЛИТИКА И ЦЕНОВАЯ СТАТИСТИКА
# ==============================================================================

@app.get("/api/analytics/price-stats")
async def api_get_price_stats(
    search_query_id: Optional[str] = None,
    query: Optional[str] = None
):
    stats = await db.get_price_stats(search_query_id=search_query_id, query=query)
    return stats.model_dump(mode="json")

@app.get("/api/analytics/price-trends")
async def api_get_price_trends(days: int = Query(default=14, ge=1, le=90)):
    trends = await db.get_price_trend_history(days=days)
    return {"trends": trends}

@app.get("/api/analytics/market-speed")
async def api_get_market_speed():
    """Статистика скорости выкупа выгодных предложений (GEM/HOT)"""
    stats = await db.get_gem_lifetime_stats()
    return {"lifetime_stats": stats}

@app.get("/api/system/telemetry")
async def api_get_telemetry():
    telemetry = await db.get_system_telemetry()
    t_dict = telemetry.model_dump(mode="json")
    t_dict["proxy_count"] = proxy_manager.total_count
    t_dict["telegram_bot_active"] = bool(config.telegram.bot_token)
    return t_dict

@app.get("/api/admin/audit-logs")
async def api_get_audit_logs(limit: int = Query(default=100, ge=1, le=500), admin: User = Depends(require_admin)):
    logs = await db.get_audit_logs(limit=limit)
    return {"logs": [l.model_dump(mode="json") for l in logs]}

@app.get("/api/blacklist/sellers")
async def api_get_blacklist_sellers():
    sellers = await db.get_blacklisted_sellers()
    return {"sellers": sellers}

class BlacklistAddRequest(BaseModel):
    seller_name: str
    reason: Optional[str] = None

@app.post("/api/blacklist/sellers")
async def api_add_blacklist_seller(req: BlacklistAddRequest):
    ok = await db.add_seller_to_blacklist(req.seller_name, req.reason or "")
    if not ok:
        raise HTTPException(status_code=400, detail="Не удалось добавить продавца")
    return {"status": "success"}

@app.delete("/api/blacklist/sellers/{seller_name}")
async def api_remove_blacklist_seller(seller_name: str):
    ok = await db.remove_seller_from_blacklist(seller_name)
    if not ok:
        raise HTTPException(status_code=404, detail="Продавец не найден в черном списке")
    return {"status": "success"}

# ==============================================================================
# REST API: ТОВАРЫ И БАЗА ДАННЫХ
# ==============================================================================

@app.get("/api/items")
async def api_get_items(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=24, ge=1, le=100),
    search_query_id: Optional[str] = None,
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
    sort_by: str = "newest"
):
    offset = (page - 1) * page_size
    items, total_count = await db.get_items_filtered(
        search_query_id=search_query_id,
        query=query,
        min_price=min_price,
        max_price=max_price,
        with_discount_only=with_discount_only,
        with_delivery_only=with_delivery_only,
        favorites_only=favorites_only,
        hot_deals_only=hot_deals_only,
        gems_only=gems_only,
        hide_reserved=hide_reserved,
        deal_grade=deal_grade,
        min_deal_score=min_deal_score,
        sort_by=sort_by,
        limit=page_size,
        offset=offset
    )
    total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
    return {
        "items": [it.model_dump(mode="json") for it in items],
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages
    }

@app.post("/api/items/recalculate-scores")
async def api_recalculate_scores():
    """Фоновый пересчет скоринга и выгоды для всех объявлений в базе"""
    updated_count = await db.recalculate_all_deal_scores()
    logger.info(f"🔄 Пересчитан AI Deal Score для {updated_count} товаров в базе")
    return {"status": "success", "updated_count": updated_count, "message": f"Скоринг обновлен для {updated_count} позиций"}

class EvaluateItemAIRequest(BaseModel):
    custom_prompt: Optional[str] = None

@app.post("/api/ai/evaluate/{item_id}")
async def api_evaluate_item_ai(item_id: str, req: Optional[EvaluateItemAIRequest] = None):
    """Генерация экспертного резюме товара через LLM"""
    item = await db.get_item_by_id(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Объявление не найдено")

    ai_settings = await db.get_ai_settings()
    if not ai_settings.enabled and not ai_settings.api_key and ai_settings.provider != "ollama":
        raise HTTPException(status_code=400, detail="AI-модуль не настроен. Укажите API-ключ в настройках.")

    # Если описание отсутствует — пробуем автоматически подтянуть детали страницы
    if not item.description:
        try:
            detailed = await browser_engine.parse_item_detail(item.url)
            if detailed:
                if detailed.description:
                    item.description = detailed.description
                if detailed.images:
                    item.images = detailed.images
                if detailed.params:
                    item.params = detailed.params
                if detailed.seller and not item.seller:
                    item.seller = detailed.seller
                await db.save_item(item)
        except Exception as e:
            logger.debug(f"Не удалось фоново подтянуть описание для {item_id}: {e}")

    market_med, market_avg = await db.get_search_market_stats(item.search_query_id)
    settings_to_use = ai_settings.model_copy()
    settings_to_use.enabled = True

    summary = await deal_scoring_engine.generate_ai_verdict(
        item=item,
        market_median=market_med,
        market_avg=market_avg,
        ai_settings=settings_to_use
    )

    if not summary:
        raise HTTPException(status_code=502, detail="Не удалось получить ответ от AI-провайдера. Проверьте API ключ и модель в настройках.")

    await db.update_item_ai_summary(item_id, summary)
    item.ai_summary = summary
    return {"status": "success", "ai_summary": summary, "item": item.model_dump(mode="json")}

@app.get("/api/items/{item_id}")
async def api_get_item(item_id: str):
    item = await db.get_item_by_id(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Объявление не найдено")
    history = await db.get_price_history(item_id=item_id)
    return {
        "item": item.model_dump(mode="json"),
        "price_history": history
    }

@app.post("/api/items/{item_id}/refresh-details")
async def api_refresh_item_details(item_id: str):
    """Детальный парсинг страницы конкретного товара с Авито (полное описание, HD фото, характеристики)"""
    item = await db.get_item_by_id(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Объявление не найдено")

    try:
        detailed_item = await browser_engine.parse_item_detail(item.url)
        if not detailed_item:
            raise HTTPException(status_code=502, detail="Не удалось загрузить страницу объявления с Авито")

        # Сохраняем search_query_id и другие метаданные
        detailed_item.search_query_id = item.search_query_id
        detailed_item.is_favorite = item.is_favorite
        detailed_item.is_hidden = item.is_hidden

        saved_item, _, _, _ = await db.save_item(detailed_item)
        logger.info(f"📄 Обновлены детальные данные и описание для лота #{item_id}")
        return {"status": "success", "item": saved_item.model_dump(mode="json")}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при обновлении деталей товара {item_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка загрузки деталей с Авито: {e}")

@app.delete("/api/items/{item_id}")
async def api_delete_item(item_id: str):
    ok = await db.delete_item(item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Объявление не найдено")
    return {"status": "success", "message": f"Объявление {item_id} удалено"}

class BatchActionRequest(BaseModel):
    item_ids: List[str]
    action: str
    search_query_id: Optional[int] = None

class BatchDeleteRequest(BaseModel):
    item_ids: List[str]
@app.post("/api/items/batch-action")
async def api_batch_action_items(req: BatchActionRequest):
    count = await db.batch_process_items(req.item_ids, req.action, req.search_query_id)
    return {"status": "success", "processed_count": count}

@app.post("/api/items/{item_id}/favorite")
async def api_toggle_item_favorite(item_id: str):
    is_fav = await db.toggle_item_favorite(item_id)
    return {"status": "success", "is_favorite": is_fav}

@app.post("/api/items/{item_id}/hide")
async def api_toggle_item_hide(item_id: str):
    is_hid = await db.toggle_item_hidden(item_id)
    return {"status": "success", "is_hidden": is_hid}

@app.post("/api/items/batch-delete")
async def api_batch_delete_items(req: BatchDeleteRequest):
    count = await db.delete_items_batch(req.item_ids)
    return {"status": "success", "deleted_count": count}
@app.post("/api/items/clear")
async def api_clear_items():
    count = await db.clear_items()
    logger.info(f"🗑 Очищена база объявлений: удалено {count} записей")
    return {"status": "success", "deleted_count": count}

@app.get("/api/export/{fmt}")
async def api_export_items(
    fmt: str,
    search_query_id: Optional[str] = None,
    query: Optional[str] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    with_discount_only: bool = False
):
    items, _ = await db.get_items_filtered(
        search_query_id=search_query_id,
        query=query,
        min_price=min_price,
        max_price=max_price,
        with_discount_only=with_discount_only,
        limit=10000
    )

    if not items:
        raise HTTPException(status_code=400, detail="Нет данных для экспорта по заданным фильтрам")

    if fmt in ("excel", "xlsx"):
        path = exporter.export_to_excel(items)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif fmt == "csv":
        path = exporter.export_to_csv(items)
        media_type = "text/csv"
    elif fmt == "json":
        path = exporter.export_to_json(items)
        media_type = "application/json"
    elif fmt in ("html", "htm"):
        path = exporter.export_to_html(items)
        media_type = "text/html"
    else:
        raise HTTPException(status_code=400, detail=f"Неподдерживаемый формат '{fmt}'. Доступны: excel, csv, json, html")
    return FileResponse(
        path=path,
        filename=path.name,
        media_type=media_type
    )


# ==============================================================================
# REST API: ЗАДАЧИ МОНИТОРИНГА
# ==============================================================================

@app.get("/api/searches")
async def api_get_searches():
    searches = await db.get_searches_with_counts()
    return searches

class SearchCreateRequest(BaseModel):
    name: str
    url: str
    min_price: Optional[int] = None
    max_price: Optional[int] = None
    check_interval_min: int = Field(default=10, ge=1, le=1440)
    enabled: bool = True
    active_hours_start: int = Field(default=0, ge=0, le=23)
    active_hours_end: int = Field(default=24, ge=1, le=24)

@app.post("/api/searches")
async def api_create_search(req: SearchCreateRequest):
    if "avito.ru" not in req.url:
        raise HTTPException(status_code=400, detail="Ссылка должна вести на домен avito.ru")

    url = req.url.strip()
    if "s=" not in url:
        url = url + ("&s=104" if "?" in url else "?s=104")

    search_id = await db.add_search(SearchQuery(
        name=req.name,
        url=url,
        min_price=req.min_price,
        max_price=req.max_price,
        check_interval_min=req.check_interval_min,
        enabled=req.enabled,
        active_hours_start=req.active_hours_start,
        active_hours_end=req.active_hours_end
    ))
    logger.info(f"➕ Добавлен поиск #{search_id} '{req.name}' ({req.active_hours_start}:00-{req.active_hours_end}:00)")
    return {"status": "success", "id": search_id}

@app.put("/api/searches/{search_id}")
async def api_update_search(search_id: int, req: SearchCreateRequest):
    search = SearchQuery(
        id=search_id,
        name=req.name,
        url=req.url,
        min_price=req.min_price,
        max_price=req.max_price,
        check_interval_min=req.check_interval_min,
        enabled=req.enabled,
        active_hours_start=req.active_hours_start,
        active_hours_end=req.active_hours_end
    )
    ok = await db.update_search(search)
    if not ok:
        raise HTTPException(status_code=404, detail="Поиск не найден")
    return {"status": "success"}

@app.patch("/api/searches/{search_id}/toggle")
async def api_toggle_search(search_id: int):
    ok = await db.toggle_search_enabled(search_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Поиск не найден")
    return {"status": "success"}

@app.delete("/api/searches/{search_id}")
async def api_delete_search(search_id: int):
    ok = await db.delete_search(search_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Поиск не найден")
    logger.info(f"🗑 Удален поиск #{search_id}")
    return {"status": "success"}

@app.post("/api/searches/{search_id}/check")
async def api_run_search_check_now(search_id: int):
    search = await db.get_search_by_id(search_id)
    if not search:
        raise HTTPException(status_code=404, detail="Поиск не найден")
    
    res = await monitor_service.check_search(search)
    return {"status": "success", "results": res}


# ==============================================================================
# REST API: УПРАВЛЕНИЕ МОНИТОРИНГОМ
# ==============================================================================

@app.get("/api/monitoring/status")
async def api_monitoring_status():
    return {
        "is_running": monitor_service.is_running,
        "last_run": monitor_service.last_run.isoformat() if monitor_service.last_run else None,
        "next_run": monitor_service.next_run.isoformat() if monitor_service.next_run else None,
        "current_checking": monitor_service.current_checking,
        "last_results": monitor_service.last_results
    }

@app.post("/api/monitoring/toggle")
async def api_monitoring_toggle():
    if monitor_service.is_running:
        await monitor_service.stop()
    else:
        await monitor_service.start()
    return {"is_running": monitor_service.is_running}

@app.post("/api/monitoring/check-all")
async def api_monitoring_check_all():
    asyncio.create_task(monitor_service.run_all_now())
    return {"status": "started", "message": "Проверка всех активных поисков запущена"}


# ==============================================================================
# REST API: ТРЕКЕР СКИДОК
# ==============================================================================

@app.get("/api/price-drops")
async def api_get_price_drops(limit: int = Query(default=50, ge=1, le=200)):
    drops = await db.get_price_drops(limit=limit)
    return drops


# ==============================================================================
# REST API & WEBSOCKET: ПАРСЕР ПОИСКОВОЙ ВЫДАЧИ
# ==============================================================================

class ParseSearchRequest(BaseModel):
    url: str
    max_pages: int = Field(default=3, ge=1, le=50)
    engine: str = Field(default="playwright", description="playwright или http")
    save_to_db: bool = True
    headless: Optional[bool] = None

@app.post("/api/parser/start")
async def api_start_parse_search(req: ParseSearchRequest):
    if "avito.ru" not in req.url:
        raise HTTPException(status_code=400, detail="Ссылка должна вести на avito.ru")

    job_id = f"job_{int(time.time() * 1000)}"
    job_manager.create_job(job_id, req.url, req.max_pages, req.engine)

    # Фоновая задача парсинга
    async def run_parser_task():
        start_t = time.time()
        await job_manager.broadcast(job_id, "log", f"🚀 Старт парсинга: {req.url} (страниц: {req.max_pages}, движок: {req.engine})")

        all_items: List[AvitoItem] = []
        try:
            if req.engine == "http":
                res = await http_engine.parse_search(req.url, max_pages=req.max_pages)
                all_items = res.items
                for err in res.errors:
                    await job_manager.broadcast(job_id, "log", f"⚠️ {err}")
            else:
                # Playwright
                engine_inst = browser_engine
                if req.headless is not None:
                    engine_inst = type(browser_engine)(headless=req.headless)

                async def progress_cb(page_num, max_p, total_so_far):
                    pct = int((page_num / max_p) * 100)
                    await job_manager.broadcast(job_id, "progress", {
                        "progress_pct": pct,
                        "current_page": page_num,
                        "items_found": total_so_far
                    })
                    await job_manager.broadcast(job_id, "log", f"📄 Страница {page_num}/{max_p}: собрано {total_so_far} объявлений")

                res = await engine_inst.parse_search(req.url, max_pages=req.max_pages, progress_callback=progress_cb)
                all_items = res.items
                for err in res.errors:
                    await job_manager.broadcast(job_id, "log", f"⚠️ {err}")

            # Сохранение в БД
            new_cnt = 0
            drop_cnt = 0
            if req.save_to_db and all_items:
                save_res = await db.save_items(all_items)
                new_cnt = save_res["new_count"]
                drop_cnt = save_res["price_drop_count"]
                await db.compute_and_update_market_averages()
                await job_manager.broadcast(job_id, "log", f"💾 Сохранено в БД: новых {new_cnt}, зафиксировано скидок {drop_cnt}")
            elapsed = round(time.time() - start_t, 2)
            await job_manager.broadcast(job_id, "complete", {
                "items": [it.model_dump(mode="json") for it in all_items],
                "total_found": len(all_items),
                "new_items_count": new_cnt,
                "price_dropped_count": drop_cnt,
                "elapsed_sec": elapsed,
                "progress_pct": 100
            })
            await job_manager.broadcast(job_id, "log", f"🎉 Парсинг завершен! Всего найдено {len(all_items)} товаров за {elapsed} сек.")

        except Exception as e:
            logger.error(f"Ошибка в задаче парсинга {job_id}: {e}")
            await job_manager.broadcast(job_id, "error", str(e))
            await job_manager.broadcast(job_id, "log", f"❌ Критическая ошибка: {e}")

    asyncio.create_task(run_parser_task())
    return {"status": "started", "job_id": job_id}

@app.websocket("/ws/parser/{job_id}")
async def ws_parser(websocket: WebSocket, job_id: str):
    token = websocket.cookies.get(SESSION_COOKIE_NAME)
    if not token or not await db.get_user_by_session(token):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    if job_id not in job_manager.subscribers:
        job_manager.subscribers[job_id] = []
    job_manager.subscribers[job_id].append(websocket)

    # Отправляем текущее состояние сразу
    if job_id in job_manager.active_jobs:
        job = job_manager.active_jobs[job_id]
        await websocket.send_text(json.dumps({"type": "init", "data": job}))

    try:
        while True:
            # Слушаем сообщения (например ping/pong)
            msg = await websocket.receive_text()
    except WebSocketDisconnect:
        if job_id in job_manager.subscribers and websocket in job_manager.subscribers[job_id]:
            job_manager.subscribers[job_id].remove(websocket)


# ==============================================================================
# REST API: ДЕТАЛЬНЫЙ ПАРСИНГ ОДНОГО ТОВАРА
# ==============================================================================

class ParseItemRequest(BaseModel):
    url: str
    save_to_db: bool = True

@app.post("/api/parser/item")
async def api_parse_item(req: ParseItemRequest):
    if "avito.ru" not in req.url:
        raise HTTPException(status_code=400, detail="Ссылка должна вести на avito.ru")

    logger.info(f"🔍 Детальный парсинг карточки: {req.url}")
    item = await browser_engine.parse_item_detail(req.url)
    if not item:
        raise HTTPException(status_code=500, detail="Не удалось извлечь данные объявления (возможно капча или страница удалена)")

    if req.save_to_db:
        await db.save_item(item)

    return {"status": "success", "item": item.model_dump(mode="json")}


# ==============================================================================
# REST API: МЕНЕДЖЕР ПРОКСИ
# ==============================================================================

@app.get("/api/proxies")
async def api_get_proxies():
    proxies = proxy_manager.proxies
    return {
        "enabled": config.proxy.enabled,
        "total_count": len(proxies),
        "proxies": proxies,
        "default_proxy": config.proxy.default_proxy,
        "proxies_file": str(proxy_manager.proxies_file)
    }

class AddProxyRequest(BaseModel):
    proxy_text: str  # может быть 1 прокси или список через перенос строки

@app.post("/api/proxies")
async def api_add_proxies(req: AddProxyRequest):
    added = 0
    lines = req.proxy_text.strip().splitlines()
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            if proxy_manager.add_proxy(line):
                added += 1
    logger.info(f"🌐 Добавлено {added} новых прокси в пул")
    return {"status": "success", "added_count": added, "total": len(proxy_manager.proxies)}

class DeleteProxyRequest(BaseModel):
    proxy: str

@app.delete("/api/proxies")
async def api_delete_proxy(req: DeleteProxyRequest):
    if req.proxy in proxy_manager.proxies:
        proxy_manager.proxies.remove(req.proxy)
        # Перезаписываем файл
        if proxy_manager.proxies_file.exists():
            with open(proxy_manager.proxies_file, "w", encoding="utf-8") as f:
                for p in proxy_manager.proxies:
                    f.write(f"{p}\n")
        logger.info(f"Удален прокси {req.proxy}")
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Прокси не найден")

@app.post("/api/proxies/check-all")
async def api_check_all_proxies():
    results = []
    
    async def check_one(p: str):
        start_t = time.time()
        ok = await proxy_manager.check_proxy(p, timeout_sec=6)
        latency = round((time.time() - start_t) * 1000)
        return {
            "proxy": p,
            "alive": ok,
            "latency_ms": latency if ok else None
        }

    tasks = [check_one(p) for p in proxy_manager.proxies]
    if tasks:
        results = await asyncio.gather(*tasks)

    alive_count = sum(1 for r in results if r["alive"])
    return {
        "total": len(results),
        "alive_count": alive_count,
        "dead_count": len(results) - alive_count,
        "results": results
    }


# ==============================================================================
# REST API: НАСТРОЙКИ И ТЕЛЕГРАМ
# ==============================================================================

@app.get("/api/settings")
async def api_get_settings():
    return {
        "scraper": {
            "headless": config.scraper.headless,
            "timeout_ms": config.scraper.timeout_ms,
            "page_delay_min": config.scraper.page_delay_min,
            "page_delay_max": config.scraper.page_delay_max,
            "max_pages": config.scraper.max_pages,
            "user_agent": config.scraper.user_agent,
            "save_cookies": config.scraper.save_cookies,
            "cookies_exist": config.scraper.cookies_file.exists()
        },
        "proxy": {
            "enabled": config.proxy.enabled,
            "default_proxy": config.proxy.default_proxy or "",
            "rotate": config.proxy.rotate,
            "count": len(proxy_manager.proxies)
        },
        "telegram": {
            "bot_token": config.telegram.bot_token,
            "admin_chat_ids": config.telegram.admin_chat_ids,
            "notification_interval_min": config.telegram.notification_interval_min,
            "send_photos": config.telegram.send_photos
        },
        "web": {
            "host": config.web.host,
            "port": config.web.port,
            "auto_open_browser": config.web.auto_open_browser
        }
    }

class SaveSettingsRequest(BaseModel):
    headless: bool
    timeout_ms: int
    page_delay_min: float
    page_delay_max: float
    proxy_enabled: bool
    default_proxy: Optional[str] = None
    bot_token: Optional[str] = None
    admin_chat_ids: List[int] = Field(default_factory=list)
    notification_interval_min: int = Field(default=10, gt=0)
    send_photos: bool = True

    @model_validator(mode="after")
    def validate_scraper(self):
        ScraperConfig(timeout_ms=self.timeout_ms, page_delay_min=self.page_delay_min,
                      page_delay_max=self.page_delay_max)
        return self

@app.post("/api/settings")
async def api_save_settings(req: SaveSettingsRequest):
    # Complete fallible work before publishing settings to running services.
    candidate = config.model_copy(deep=True)
    candidate.scraper.headless = req.headless
    candidate.scraper.timeout_ms = req.timeout_ms
    candidate.scraper.page_delay_min = req.page_delay_min
    candidate.scraper.page_delay_max = req.page_delay_max
    
    candidate.proxy.enabled = req.proxy_enabled
    candidate.proxy.default_proxy = (req.default_proxy or "").strip() or None
    
    candidate.telegram.bot_token = req.bot_token or ""
    candidate.telegram.admin_chat_ids = req.admin_chat_ids
    candidate.telegram.notification_interval_min = req.notification_interval_min
    candidate.telegram.send_photos = req.send_photos

    try:
        staged_proxies = ProxyManager(proxies_file=candidate.proxy.proxies_file,
                                      default_proxy=candidate.proxy.default_proxy or "")
        candidate.save_to_env()
    except OSError:
        raise HTTPException(status_code=500, detail="Не удалось сохранить настройки. Проверьте доступ к файлам конфигурации.") from None

    config.scraper = candidate.scraper
    config.proxy = candidate.proxy
    config.telegram = candidate.telegram
    proxy_manager.default_proxy = candidate.proxy.default_proxy
    proxy_manager.proxies = staged_proxies.proxies
    proxy_manager._current_index = 0
    active_proxy = proxy_manager.get_proxy() if config.proxy.enabled else None
    browser_engine.headless = config.scraper.headless
    browser_engine.proxy_str = active_proxy
    http_engine.proxy = active_proxy
    logger.info("⚙️ Настройки успешно обновлены и сохранены в .env")
    return {"status": "success", "message": "Настройки сохранены"}

class TestTelegramRequest(BaseModel):
    bot_token: Optional[str] = None
    chat_id: int

@app.post("/api/telegram/test")
async def api_test_telegram(req: TestTelegramRequest):
    token = req.bot_token or config.telegram.bot_token
    if not token:
        raise HTTPException(status_code=400, detail="Токен Telegram бота не указан")

    text = (
        "🤖 <b>Тестовое уведомление Avito Parser Pro</b>\n\n"
        "✅ Подключение к веб-панели работает отлично!\n"
        f"📅 Время проверки: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
    )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": req.chat_id,
        "text": text,
        "parse_mode": "HTML"
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, data=payload)
            data = resp.json()
            if data.get("ok"):
                return {"status": "success", "message": "Тестовое сообщение успешно отправлено!"}
            else:
                desc = data.get("description", "Неизвестная ошибка Telegram API")
                raise HTTPException(status_code=400, detail=f"Ошибка Telegram: {desc}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Не удалось связаться с Telegram: {e}")

@app.post("/api/settings/clear-cookies")
async def api_clear_cookies():
    if config.scraper.cookies_file.exists():
        config.scraper.cookies_file.unlink()
        logger.info("🍪 Файл cookies.json успешно очищен")
        return {"status": "success", "message": "Куки очищены"}
    return {"status": "info", "message": "Файл кук не был создан"}

# ==============================================================================
# REST API: НАСТРОЙКИ AI И LLM ЭКСПЕРТИЗЫ
# ==============================================================================

class SaveAISettingsRequest(BaseModel):
    enabled: bool = False
    provider: str = "deepseek"
    api_key: str = ""
    model: str = "deepseek-chat"
    api_base: str = "https://api.deepseek.com"
    prompt_template: Optional[str] = None

class TestAIRequest(BaseModel):
    provider: str = "deepseek"
    api_key: str = ""
    model: str = "deepseek-chat"
    api_base: str = "https://api.deepseek.com"

@app.get("/api/ai/settings")
async def api_get_ai_settings():
    settings = await db.get_ai_settings()
    return {"settings": settings.model_dump(mode="json")}

@app.post("/api/ai/settings")
async def api_save_ai_settings(req: SaveAISettingsRequest):
    settings = AISettings(
        enabled=req.enabled,
        provider=req.provider,
        api_key=req.api_key.strip(),
        model=req.model.strip(),
        api_base=req.api_base.strip(),
        prompt_template=req.prompt_template
    )
    saved = await db.save_ai_settings(settings)
    logger.info(f"🧠 Настройки AI обновлены: провайдер={saved.provider}, модель={saved.model}, enabled={saved.enabled}")
    return {"status": "success", "settings": saved.model_dump(mode="json"), "message": "Настройки AI успешно сохранены"}

@app.post("/api/ai/test")
async def api_test_ai_connection(req: TestAIRequest):
    provider = (req.provider or "deepseek").lower()
    api_key = req.api_key.strip()
    model = req.model.strip() or "deepseek-chat"
    api_base = req.api_base.strip().rstrip("/")

    if not api_base:
        if provider == "deepseek":
            api_base = "https://api.deepseek.com"
        elif provider == "openai":
            api_base = "https://api.openai.com/v1"
        elif provider == "openrouter":
            api_base = "https://openrouter.ai/api/v1"
        elif provider == "ollama":
            api_base = "http://localhost:11434/v1"
        else:
            api_base = "https://api.deepseek.com"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Ты тестовый помощник. Ответь одним кратким предложением 'Подключение к AI успешно установлено!'."},
            {"role": "user", "content": "Тест подключения"}
        ],
        "max_tokens": 50,
        "temperature": 0.1
    }

    url = f"{api_base}/chat/completions"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                reply = data.get("choices", [{}])[0].get("message", {}).get("content", "OK")
                return {
                    "status": "success",
                    "message": "Соединение с AI успешно установлено!",
                    "provider": provider,
                    "model": model,
                    "response": reply
                }
            else:
                error_msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
                raise HTTPException(status_code=400, detail=f"Ошибка AI-провайдера: {error_msg}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Не удалось подключиться к {url}: {e}")


# ==============================================================================
# REST API: ПОЛЬЗОВАТЕЛЬСКИЕ ПРАВИЛА СКОРИНГА
# ==============================================================================

class CustomRuleCreateRequest(BaseModel):
    pattern: str
    label: str
    score_delta: int = -25

@app.get("/api/scoring/rules")
async def api_get_scoring_rules(active_only: bool = False):
    rules = await db.get_custom_scoring_rules(active_only=active_only)
    return {"rules": rules}

@app.post("/api/scoring/rules")
async def api_add_scoring_rule(req: CustomRuleCreateRequest):
    if not req.pattern.strip():
        raise HTTPException(status_code=400, detail="Паттерн не может быть пустым")
    if not req.label.strip():
        raise HTTPException(status_code=400, detail="Описание правила не может быть пустым")
    rule_id = await db.add_custom_scoring_rule(req.pattern, req.label, req.score_delta)
    logger.info(f"📐 Добавлено правило скоринга #{rule_id}: '{req.label}' ({req.score_delta:+d} баллов)")
    return {"status": "success", "id": rule_id}

@app.delete("/api/scoring/rules/{rule_id}")
async def api_delete_scoring_rule(rule_id: int):
    ok = await db.delete_custom_scoring_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    logger.info(f"🗑 Удалено правило скоринга #{rule_id}")
    return {"status": "success"}

@app.patch("/api/scoring/rules/{rule_id}/toggle")
async def api_toggle_scoring_rule(rule_id: int):
    ok = await db.toggle_custom_scoring_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    return {"status": "success"}


# ==============================================================================
# REST API: НАСТРОЙКИ WEBHOOK
# ==============================================================================

class SaveWebhookSettingsRequest(BaseModel):
    url: str = ""
    enabled: bool = False
    send_new: bool = True
    send_drops: bool = True
    min_deal_score: int = 0
    secret: str = ""

@app.get("/api/webhook/settings")
async def api_get_webhook_settings():
    settings = await db.get_webhook_settings()
    return {"settings": settings.model_dump(mode="json")}

@app.post("/api/webhook/settings")
async def api_save_webhook_settings(req: SaveWebhookSettingsRequest):
    from models import WebhookSettings
    settings = WebhookSettings(
        url=req.url.strip(),
        enabled=req.enabled,
        send_new=req.send_new,
        send_drops=req.send_drops,
        min_deal_score=req.min_deal_score,
        secret=req.secret.strip()
    )
    saved = await db.save_webhook_settings(settings)
    logger.info(f"🔗 Настройки Webhook сохранены: URL={saved.url}, enabled={saved.enabled}")
    return {"status": "success", "settings": saved.model_dump(mode="json"), "message": "Настройки Webhook успешно сохранены"}

@app.post("/api/webhook/test")
async def api_test_webhook():
    settings = await db.get_webhook_settings()
    if not settings.url:
        raise HTTPException(status_code=400, detail="Webhook URL не указан")

    test_payload = {
        "event": "test",
        "message": "Тестовое уведомление от Avito Parser Pro",
        "timestamp": datetime.now().isoformat()
    }
    headers = {"Content-Type": "application/json"}
    body_bytes = json.dumps(test_payload, ensure_ascii=False).encode("utf-8")
    if settings.secret:
        sig = hmac.new(settings.secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
        headers["X-Webhook-Signature"] = sig

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(settings.url, content=body_bytes, headers=headers)
            return {
                "status": "success",
                "status_code": resp.status_code,
                "response": resp.text[:200]
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Не удалось отправить тестовый Webhook: {e}")

# ==============================================================================
# REST API: ЛОГИ
# ==============================================================================

@app.get("/api/logs")
async def api_get_logs():
    return {"logs": log_buffer.records}

@app.delete("/api/logs")
async def api_clear_logs():
    log_buffer.clear()
    return {"status": "success"}


# ==============================================================================
# ЗАПУСК СЕРВЕРА
# ==============================================================================

def run_server(host: Optional[str] = None, port: Optional[int] = None, open_browser: bool = True):
    h = host or config.web.host
    p = port or config.web.port
    browser_host = "127.0.0.1" if h in ("0.0.0.0", "::") else h

    if open_browser and config.web.auto_open_browser:
        import webbrowser
        def _open():
            time.sleep(1.2)
            webbrowser.open(f"http://{browser_host}:{p}")
        import threading
        threading.Thread(target=_open, daemon=True).start()

    print(f"\n🌐 [Avito Max Parser] Веб-панель доступна по адресу: http://{browser_host}:{p}\n")
    uvicorn.run("web_server:app", host=h, port=p, reload=False, log_level="info")

if __name__ == "__main__":
    run_server()
