import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from database import Database, db
from config import config
from models import AISettings, AvitoItem, SearchQuery, SellerInfo, User
from exporter import exporter
from browser_engine import browser_engine
from http_engine import http_engine
from proxy_manager import proxy_manager
from ai_scoring import deal_scoring_engine
from monitoring import monitor_service
from security import avito_url
from auth_dependencies import require_admin
from dependencies import get_database, get_monitoring_service

router = APIRouter()
logger = logging.getLogger("AvitoAPI")


@router.get("/api/monitoring/status")
async def api_monitoring_status(
    db: Database = Depends(get_database),
    monitor_service=Depends(get_monitoring_service),
):
    return {
        "is_running": monitor_service.is_running,
        "last_run": monitor_service.last_run.isoformat()
        if monitor_service.last_run
        else None,
        "next_run": monitor_service.next_run.isoformat()
        if monitor_service.next_run
        else None,
        "current_checking": monitor_service.current_checking,
        "last_results": monitor_service.last_results,
    }


@router.get("/api/monitoring/runs")
async def api_monitoring_runs(
    search_id: Optional[int] = Query(default=None, ge=1),
    outcome: Optional[str] = Query(default=None, pattern="^(success|empty|partial|blocked|error|skipped)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Database = Depends(get_database),
):
    return {
        "runs": await db.get_monitoring_runs(search_id, outcome, limit, offset),
        "stats": await db.get_monitoring_run_stats(),
    }


@router.post("/api/monitoring/toggle")
async def api_monitoring_toggle(
    db: Database = Depends(get_database),
    monitor_service=Depends(get_monitoring_service),
):
    if monitor_service.is_running:
        await monitor_service.stop()
    else:
        await monitor_service.start()
    return {"is_running": monitor_service.is_running}


@router.post("/api/monitoring/check-all")
async def api_monitoring_check_all(
    db: Database = Depends(get_database),
    monitor_service=Depends(get_monitoring_service),
):
    asyncio.create_task(monitor_service.run_all_now())
    return {"status": "started", "message": "Проверка всех активных поисков запущена"}


# ==============================================================================
# REST API: ТРЕКЕР СКИДОК
# ==============================================================================


@router.get("/api/price-drops")
async def api_get_price_drops(
    limit: int = Query(default=50, ge=1, le=200),
    db: Database = Depends(get_database),
    monitor_service=Depends(get_monitoring_service),
):
    drops = await db.get_price_drops(limit=limit)
    return drops


# ==============================================================================
# REST API & WEBSOCKET: ПАРСЕР ПОИСКОВОЙ ВЫДАЧИ
# ==============================================================================
