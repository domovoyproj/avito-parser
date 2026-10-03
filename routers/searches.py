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


class SearchCreateRequest(BaseModel):
    name: str
    url: str
    min_price: Optional[int] = None
    max_price: Optional[int] = None
    check_interval_min: int = Field(default=10, ge=1, le=1440)
    enabled: bool = True
    active_hours_start: int = Field(default=0, ge=0, le=23)
    active_hours_end: int = Field(default=24, ge=1, le=24)


@router.post("/api/searches")
async def api_create_search(
    req: SearchCreateRequest, db: Database = Depends(get_database)
):
    if not avito_url(req.url):
        raise HTTPException(
            status_code=400, detail="Ссылка должна вести на домен avito.ru"
        )

    url = req.url.strip()
    if "s=" not in url:
        url = url + ("&s=104" if "?" in url else "?s=104")

    search_id = await db.add_search(
        SearchQuery(
            name=req.name,
            url=url,
            min_price=req.min_price,
            max_price=req.max_price,
            check_interval_min=req.check_interval_min,
            enabled=req.enabled,
            active_hours_start=req.active_hours_start,
            active_hours_end=req.active_hours_end,
        )
    )
    logger.info(
        f"➕ Добавлен поиск #{search_id} '{req.name}' ({req.active_hours_start}:00-{req.active_hours_end}:00)"
    )
    return {"status": "success", "id": search_id}


@router.put("/api/searches/{search_id}")
async def api_update_search(
    search_id: int, req: SearchCreateRequest, db: Database = Depends(get_database)
):
    search = SearchQuery(
        id=search_id,
        name=req.name,
        url=req.url,
        min_price=req.min_price,
        max_price=req.max_price,
        check_interval_min=req.check_interval_min,
        enabled=req.enabled,
        active_hours_start=req.active_hours_start,
        active_hours_end=req.active_hours_end,
    )
    ok = await db.update_search(search)
    if not ok:
        raise HTTPException(status_code=404, detail="Поиск не найден")
    return {"status": "success"}


@router.patch("/api/searches/{search_id}/toggle")
async def api_toggle_search(search_id: int, db: Database = Depends(get_database)):
    ok = await db.toggle_search_enabled(search_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Поиск не найден")
    return {"status": "success"}


@router.delete("/api/searches/{search_id}")
async def api_delete_search(search_id: int, db: Database = Depends(get_database)):
    ok = await db.delete_search(search_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Поиск не найден")
    logger.info(f"🗑 Удален поиск #{search_id}")
    return {"status": "success"}


@router.post("/api/searches/{search_id}/check")
async def api_run_search_check_now(
    search_id: int, db: Database = Depends(get_database)
):
    search = await db.get_search_by_id(search_id)
    if not search:
        raise HTTPException(status_code=404, detail="Поиск не найден")

    res = await monitor_service.check_search(search)
    return {"status": "success", "results": res}


# ==============================================================================
# REST API: УПРАВЛЕНИЕ МОНИТОРИНГОМ
# ==============================================================================
