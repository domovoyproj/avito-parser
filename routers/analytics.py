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


@router.get("/api/dashboard")
async def api_dashboard(db: Database = Depends(get_database)):
    data = await db.get_dashboard_summary()
    data["monitoring"] = {
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
    data["proxy_count"] = proxy_manager.total_count
    data["telegram_configured"] = bool(config.telegram.bot_token)
    try:
        data["lifetime_stats"] = await db.get_gem_lifetime_stats()
    except Exception:
        data["lifetime_stats"] = {
            "count": 0,
            "avg_hours": 0.0,
            "median_hours": 0.0,
            "min_hours": 0.0,
            "max_hours": 0.0,
        }
    return data


@router.get("/api/stats")
async def api_stats(db: Database = Depends(get_database)):
    return await db.get_stats()


# ==============================================================================
# REST API: АНАЛИТИКА И ЦЕНОВАЯ СТАТИСТИКА
# ==============================================================================


@router.get("/api/analytics/price-stats")
async def api_get_price_stats(
    search_query_id: Optional[str] = None,
    query: Optional[str] = None,
    db: Database = Depends(get_database),
):
    stats = await db.get_price_stats(search_query_id=search_query_id, query=query)
    return stats.model_dump(mode="json")


@router.get("/api/analytics/price-trends")
async def api_get_price_trends(
    days: int = Query(default=14, ge=1, le=90), db: Database = Depends(get_database)
):
    trends = await db.get_price_trend_history(days=days)
    return {"trends": trends}


@router.get("/api/analytics/market-speed")
async def api_get_market_speed(db: Database = Depends(get_database)):
    """Статистика скорости выкупа выгодных предложений (GEM/HOT)"""
    stats = await db.get_gem_lifetime_stats()
    return {"lifetime_stats": stats}


@router.get("/api/system/telemetry")
async def api_get_telemetry(db: Database = Depends(get_database)):
    telemetry = await db.get_system_telemetry()
    t_dict = telemetry.model_dump(mode="json")
    t_dict["proxy_count"] = proxy_manager.total_count
    t_dict["telegram_bot_active"] = bool(config.telegram.bot_token)
    return t_dict


@router.get("/api/admin/audit-logs")
async def api_get_audit_logs(
    limit: int = Query(default=100, ge=1, le=500),
    admin: User = Depends(require_admin),
    db: Database = Depends(get_database),
):
    logs = await db.get_audit_logs(limit=limit)
    return {"logs": [l.model_dump(mode="json") for l in logs]}


@router.get("/api/blacklist/sellers")
async def api_get_blacklist_sellers(db: Database = Depends(get_database)):
    sellers = await db.get_blacklisted_sellers()
    return {"sellers": sellers}


class BlacklistAddRequest(BaseModel):
    seller_name: str
    reason: Optional[str] = None


@router.post("/api/blacklist/sellers")
async def api_add_blacklist_seller(
    req: BlacklistAddRequest, db: Database = Depends(get_database)
):
    ok = await db.add_seller_to_blacklist(req.seller_name, req.reason or "")
    if not ok:
        raise HTTPException(status_code=400, detail="Не удалось добавить продавца")
    return {"status": "success"}


@router.delete("/api/blacklist/sellers/{seller_name}")
async def api_remove_blacklist_seller(
    seller_name: str, db: Database = Depends(get_database)
):
    ok = await db.remove_seller_from_blacklist(seller_name)
    if not ok:
        raise HTTPException(
            status_code=404, detail="Продавец не найден в черном списке"
        )
    return {"status": "success"}


# ==============================================================================
# REST API: ТОВАРЫ И БАЗА ДАННЫХ
# ==============================================================================
