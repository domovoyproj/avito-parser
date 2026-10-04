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
from parser_jobs import ParsingJobManager, job_manager

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
    from export_jobs import export_jobs
    export_task = asyncio.create_task(export_jobs.run())
    try:
        yield
    finally:
        export_task.cancel()
        await asyncio.gather(export_task, return_exceptions=True)
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
        oldest = await (await connection.execute("SELECT MIN(NULLIF(created_at,0)) FROM notification_outbox WHERE status='pending'")).fetchone()
        count = await (await connection.execute('SELECT COUNT(*) FROM items')).fetchone()
    return {'outbox': dict(rows), 'oldest_pending_at': oldest[0], 'oldest_pending_age_seconds': max(0,time.time()-oldest[0]) if oldest[0] else None, 'items': count[0],
            'database_bytes': config.db_path.stat().st_size if config.db_path.exists() else 0,
            'active_parser_jobs': len(job_manager.tasks),
            'last_monitoring_cycle': monitor_service.last_run.isoformat() if monitor_service.last_run else None,
            'last_monitoring_duration_seconds': monitor_service.last_duration_seconds,
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

@app.get("/watchlists", response_class=HTMLResponse)
async def page_watchlists(request: Request):
    return await render_auth_page("watchlists")(request)

@app.get("/feedback", response_class=HTMLResponse)
async def page_feedback(request: Request):
    return await render_auth_page("feedback")(request)


@app.get('/exports', response_class=HTMLResponse)
async def page_exports(request: Request):
    return await render_auth_page('exports')(request)

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

from routers.analytics import router as analytics_router
app.include_router(analytics_router)
from routers.items import router as items_router
app.include_router(items_router)
from routers.exports import router as exports_router
app.include_router(exports_router)
from routers.searches import router as searches_router
app.include_router(searches_router)
from routers.monitoring import router as monitoring_router
app.include_router(monitoring_router)
from routers.watchlists import router as watchlists_router
app.include_router(watchlists_router)
from routers.feedback import router as feedback_router
app.include_router(feedback_router)
from routers.saved_filters import router as saved_filters_router
app.include_router(saved_filters_router)

from routers.parser import router as parser_router, ws_parser, ParseSearchRequest, ParseItemRequest
app.include_router(parser_router)

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
