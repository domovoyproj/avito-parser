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
from auth_dependencies import require_admin, require_auth
from dependencies import get_database, get_monitoring_service

router = APIRouter()
logger = logging.getLogger("AvitoAPI")


@router.get("/api/items")
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
    sort_by: str = "newest",
    db: Database = Depends(get_database),
    user: User = Depends(require_auth),
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
        offset=offset,
        user_id=user.id,
    )
    total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
    return {
        "items": [it.model_dump(mode="json") for it in items],
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


@router.post("/api/items/recalculate-scores")
async def api_recalculate_scores(db: Database = Depends(get_database)):
    """Фоновый пересчет скоринга и выгоды для всех объявлений в базе"""
    updated_count = await db.recalculate_all_deal_scores()
    logger.info(f"🔄 Пересчитан AI Deal Score для {updated_count} товаров в базе")
    return {
        "status": "success",
        "updated_count": updated_count,
        "message": f"Скоринг обновлен для {updated_count} позиций",
    }


class EvaluateItemAIRequest(BaseModel):
    custom_prompt: Optional[str] = None


@router.post("/api/ai/evaluate/{item_id}")
async def api_evaluate_item_ai(
    item_id: str,
    req: Optional[EvaluateItemAIRequest] = None,
    db: Database = Depends(get_database),
):
    """Генерация экспертного резюме товара через LLM"""
    item = await db.get_item_by_id(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Объявление не найдено")

    ai_settings = await db.get_ai_settings()
    if (
        not ai_settings.enabled
        and not ai_settings.api_key
        and ai_settings.provider != "ollama"
    ):
        raise HTTPException(
            status_code=400,
            detail="AI-модуль не настроен. Укажите API-ключ в настройках.",
        )

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
        ai_settings=settings_to_use,
    )

    if not summary:
        raise HTTPException(
            status_code=502,
            detail="Не удалось получить ответ от AI-провайдера. Проверьте API ключ и модель в настройках.",
        )

    await db.update_item_ai_summary(item_id, summary)
    item.ai_summary = summary
    return {
        "status": "success",
        "ai_summary": summary,
        "item": item.model_dump(mode="json"),
    }


@router.get("/api/items/{item_id}")
async def api_get_item(item_id: str, db: Database = Depends(get_database), user: User = Depends(require_auth)):
    item = await db.get_item_by_id(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Объявление не найдено")
    item.is_favorite = item_id in await db.personal_favorite_ids(user.id, [item_id])
    history = await db.get_price_history(item_id=item_id)
    return {"item": item.model_dump(mode="json"), "price_history": history}


@router.post("/api/items/{item_id}/refresh-details")
async def api_refresh_item_details(item_id: str, db: Database = Depends(get_database)):
    """Детальный парсинг страницы конкретного товара с Авито (полное описание, HD фото, характеристики)"""
    item = await db.get_item_by_id(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Объявление не найдено")

    try:
        detailed_item = await browser_engine.parse_item_detail(item.url)
        if not detailed_item:
            raise HTTPException(
                status_code=502,
                detail="Не удалось загрузить страницу объявления с Авито",
            )

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
        raise HTTPException(
            status_code=500, detail=f"Ошибка загрузки деталей с Авито: {e}"
        )


@router.delete("/api/items/{item_id}")
async def api_delete_item(item_id: str, db: Database = Depends(get_database)):
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


@router.post("/api/items/batch-action")
async def api_batch_action_items(
    req: BatchActionRequest, db: Database = Depends(get_database), user: User = Depends(require_auth)
):
    if req.action in ("favorite", "unfavorite"):
        count = await db.set_personal_favorites(user.id, req.item_ids, req.action == "favorite")
    else:
        count = await db.batch_process_items(req.item_ids, req.action, req.search_query_id)
    return {"status": "success", "processed_count": count}


@router.post("/api/items/{item_id}/favorite")
async def api_toggle_item_favorite(item_id: str, db: Database = Depends(get_database), user: User = Depends(require_auth)):
    if not await db.get_item_by_id(item_id):
        raise HTTPException(status_code=404, detail="Объявление не найдено")
    is_fav = await db.toggle_personal_favorite(user.id, item_id)
    return {"status": "success", "is_favorite": is_fav}


@router.post("/api/items/{item_id}/hide")
async def api_toggle_item_hide(item_id: str, db: Database = Depends(get_database)):
    is_hid = await db.toggle_item_hidden(item_id)
    return {"status": "success", "is_hidden": is_hid}


@router.post("/api/items/batch-delete")
async def api_batch_delete_items(
    req: BatchDeleteRequest, db: Database = Depends(get_database)
):
    count = await db.delete_items_batch(req.item_ids)
    return {"status": "success", "deleted_count": count}


@router.post("/api/items/clear")
async def api_clear_items(db: Database = Depends(get_database)):
    count = await db.clear_items()
    logger.info(f"🗑 Очищена база объявлений: удалено {count} записей")
    return {"status": "success", "deleted_count": count}


@router.get("/api/export/{fmt}")
async def api_export_items(
    fmt: str,
    search_query_id: Optional[str] = None,
    query: Optional[str] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    with_discount_only: bool = False,
    db: Database = Depends(get_database),
):
    items, total = await db.get_items_filtered(
        search_query_id=search_query_id,
        query=query,
        min_price=min_price,
        max_price=max_price,
        with_discount_only=with_discount_only,
        limit=10000,
    )

    if total > 10000:
        raise HTTPException(
            status_code=413,
            detail="Экспорт ограничен 10 000 объявлений. Уточните фильтры.",
        )

    if not items:
        raise HTTPException(
            status_code=400, detail="Нет данных для экспорта по заданным фильтрам"
        )

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
        raise HTTPException(
            status_code=400,
            detail=f"Неподдерживаемый формат '{fmt}'. Доступны: excel, csv, json, html",
        )
    return FileResponse(path=path, filename=path.name, media_type=media_type)


# ==============================================================================
# REST API: ЗАДАЧИ МОНИТОРИНГА
# ==============================================================================


@router.get("/api/searches")
async def api_get_searches(db: Database = Depends(get_database)):
    searches = await db.get_searches_with_counts()
    return searches
