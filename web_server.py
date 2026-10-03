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
import secrets
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
from security import login_limiter, same_origin, avito_url
from observability import RedactingFilter, redact

# --- Логирование и кольцевой буфер для Веб-панели ---
class WebLogBuffer(logging.Handler):
    def __init__(self, max_records: int = 300):
        super().__init__()
        self.max_records = max_records
        self.records: List[Dict[str, Any]] = []

    def emit(self, record: logging.LogRecord):
        try:
            msg = redact(self.format(record))
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
for handler in root_logger.handlers:
    handler.addFilter(RedactingFilter())

logger = logging.getLogger("AvitoWeb")

# --- Сервис фонового мониторинга ---
from monitoring import MonitoringService, monitor_service

# --- Менеджер активных парсинг-сессий (WebSockets) ---
class ParsingJobManager:
    def __init__(self):
        self.active_jobs: Dict[str, Dict[str, Any]] = {}
        self.subscribers: Dict[str, List[WebSocket]] = {}
        self.tasks = {}

    def create_job(self, job_id: str, url: str, max_pages: int, engine: str) -> Dict[str, Any]:
        for key, old in list(self.active_jobs.items()):
            if key not in self.tasks and time.time() - old['start_time'] > 3600:
                self.active_jobs.pop(key, None)
                self.subscribers.pop(key, None)
        if len(self.tasks) >= 3 or len(self.active_jobs) >= 100:
            raise HTTPException(status_code=429, detail='Достигнут лимит задач парсинга')
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
                self.active_jobs[job_id]['logs'] = self.active_jobs[job_id]['logs'][-500:]
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
    from outbox import outbox_worker
    outbox_task = asyncio.create_task(outbox_worker.run())
    try:
        yield
    finally:
        for task in list(job_manager.tasks.values()):
            task.cancel()
        await asyncio.gather(*job_manager.tasks.values(), return_exceptions=True)
        outbox_task.cancel()
        try:
            await outbox_task
        except asyncio.CancelledError:
            pass
        await monitor_service.stop()
    logger.info("Веб-панель Avito Parser остановлена")

app = FastAPI(
    title="Avito Parser Pro Web Panel",
    description="Современная веб-панель управления парсером, базой объявлений и мониторингом Авито",
    version="2.0.0",
    lifespan=lifespan
)


@app.get('/health', include_in_schema=False)
async def health():
    return {'status': 'ok'}


@app.get('/ready', include_in_schema=False)
async def ready():
    try:
        async with db.connection() as connection:
            await connection.execute('SELECT 1 FROM schema_migrations LIMIT 1')
        return {'status': 'ready'}
    except Exception:
        return JSONResponse(status_code=503, content={'status': 'unavailable'})


@app.get('/api/metrics')
async def operational_metrics():
    async with db.connection() as connection:
        rows = await (await connection.execute('SELECT status, COUNT(*) FROM notification_outbox GROUP BY status')).fetchall()
        oldest = await (await connection.execute("SELECT MIN(available_at) FROM notification_outbox WHERE status='pending'")).fetchone()
        count = await (await connection.execute('SELECT COUNT(*) FROM items')).fetchone()
    return {'outbox': dict(rows), 'oldest_pending_at': oldest[0], 'items': count[0],
            'database_bytes': config.db_path.stat().st_size if config.db_path.exists() else 0,
            'active_parser_jobs': len(job_manager.tasks),
            'last_monitoring_cycle': monitor_service.last_run.isoformat() if monitor_service.last_run else None,
            'last_monitoring_results': monitor_service.last_results}

app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
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

from auth_dependencies import SESSION_COOKIE_NAME, CSRF_COOKIE_NAME, get_optional_user, require_auth, require_admin

@app.middleware("http")
async def protect_api(request: Request, call_next):
    """Apply session authentication to the complete API, including exports."""
    path = request.url.path
    if path.startswith("/api/"):
        unsafe = request.method not in ("GET", "HEAD", "OPTIONS")
        origin = request.headers.get("origin")
        if unsafe and ((origin and not same_origin(origin, request.url)) or request.headers.get("sec-fetch-site") == "cross-site"):
            return JSONResponse(status_code=403, content={"detail": "Недопустимый источник запроса"})
        if path == "/api/auth/login":
            return await call_next(request)
        user = await get_optional_user(request)
        if not user:
            return JSONResponse(status_code=401, content={"detail": "Требуется авторизация"})
        if unsafe and request.cookies.get(SESSION_COOKIE_NAME):
            csrf = request.cookies.get(CSRF_COOKIE_NAME, "")
            if not csrf or not hmac.compare_digest(csrf, request.headers.get("x-csrf-token", "")):
                return JSONResponse(status_code=403, content={"detail": "Недопустимый CSRF-токен"})
        admin_paths = ("/api/admin/", "/api/settings", "/api/ai/", "/api/proxies", "/api/telegram/", "/api/webhook", "/api/scoring/", "/api/logs", "/api/metrics")
        if path.startswith(admin_paths) and user.role != UserRole.ADMIN:
            return JSONResponse(status_code=403, content={"detail": "Требуются права администратора"})
        if unsafe and user.role == UserRole.VIEWER and not path.startswith("/api/auth/"):
            return JSONResponse(status_code=403, content={"detail": "Доступ только для просмотра"})
    response = await call_next(request)
    if path.startswith("/api/") and request.method == "GET" and request.cookies.get(SESSION_COOKIE_NAME) and not request.cookies.get(CSRF_COOKIE_NAME):
        response.set_cookie(CSRF_COOKIE_NAME, secrets.token_urlsafe(32), secure=config.web.secure_cookies,
                            samesite="strict", path="/")
    return response

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

from routers.auth import router as auth_router
app.include_router(auth_router)

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
    items, total = await db.get_items_filtered(
        search_query_id=search_query_id,
        query=query,
        min_price=min_price,
        max_price=max_price,
        with_discount_only=with_discount_only,
        limit=10000
    )

    if total > 10000:
        raise HTTPException(status_code=413, detail="Экспорт ограничен 10 000 объявлений. Уточните фильтры.")

    if not items:
        raise HTTPException(status_code=400, detail="Нет данных для экспорта по заданным фильтрам")

    if fmt in ("excel", "xlsx"):
        path = await asyncio.to_thread(exporter.export_to_excel, items)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif fmt == "csv":
        path = await asyncio.to_thread(exporter.export_to_csv, items)
        media_type = "text/csv"
    elif fmt == "json":
        path = await asyncio.to_thread(exporter.export_to_json, items)
        media_type = "application/json"
    elif fmt in ("html", "htm"):
        path = await asyncio.to_thread(exporter.export_to_html, items)
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
    if not avito_url(req.url):
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
    if not avito_url(req.url):
        raise HTTPException(status_code=400, detail="Ссылка должна вести на avito.ru")

    if req.engine not in ('http', 'playwright'):
        raise HTTPException(status_code=422, detail='Неизвестный движок')
    job_id = 'job_' + secrets.token_hex(12)
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

    task = asyncio.create_task(asyncio.wait_for(run_parser_task(), timeout=180))
    job_manager.tasks[job_id] = task
    def finished(done):
        job_manager.tasks.pop(job_id, None)
        if done.cancelled():
            job_manager.active_jobs[job_id]['status'] = 'cancelled'
        elif done.exception():
            job_manager.active_jobs[job_id]['status'] = 'error'
            job_manager.active_jobs[job_id]['errors'].append(type(done.exception()).__name__)
    task.add_done_callback(finished)
    return {"status": "started", "job_id": job_id}


@app.post('/api/parser/{job_id}/cancel')
async def cancel_parser(job_id: str):
    task = job_manager.tasks.get(job_id)
    if not task:
        raise HTTPException(status_code=404, detail='Активная задача не найдена')
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return {'status': 'cancelled'}

@app.websocket("/ws/parser/{job_id}")
async def ws_parser(websocket: WebSocket, job_id: str):
    origin = websocket.headers.get("origin")
    expected = str(websocket.url).replace("ws://", "http://", 1).replace("wss://", "https://", 1)
    if origin and not same_origin(origin, expected):
        await websocket.close(code=1008)
        return
    token = websocket.cookies.get(SESSION_COOKIE_NAME)
    if not token or not await db.get_user_by_session(token):
        await websocket.close(code=1008)
        return
    if job_id not in job_manager.active_jobs:
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
            if not await db.get_user_by_session(token):
                await websocket.close(code=1008)
                break
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=5)
            except asyncio.TimeoutError:
                continue
    except WebSocketDisconnect:
        pass
    finally:
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
    if not avito_url(req.url):
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

from routers.settings import router as settings_router, SaveSettingsRequest
app.include_router(settings_router)

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
