import asyncio
import time
import json
import secrets
import logging
from typing import Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from models import AvitoItem
from database import Database
from parser_jobs import ParsingJobManager
from security import avito_url, same_origin
from auth_dependencies import SESSION_COOKIE_NAME
from dependencies import get_database, get_parser_jobs, get_http_engine, get_browser_engine
router=APIRouter()
logger=logging.getLogger('AvitoParserAPI')

class ParseSearchRequest(BaseModel):
    url: str
    max_pages: int = Field(default=3, ge=1, le=50)
    engine: str = Field(default="playwright", description="playwright или http")
    save_to_db: bool = True
    headless: Optional[bool] = None

@router.post("/api/parser/start")
async def api_start_parse_search(req: ParseSearchRequest, db: Database = Depends(get_database), job_manager: ParsingJobManager = Depends(get_parser_jobs), http_engine=Depends(get_http_engine), browser_engine=Depends(get_browser_engine)):
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


@router.post('/api/parser/{job_id}/cancel')
async def cancel_parser(job_id: str, job_manager: ParsingJobManager = Depends(get_parser_jobs)):
    task = job_manager.tasks.get(job_id)
    if not task:
        raise HTTPException(status_code=404, detail='Активная задача не найдена')
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return {'status': 'cancelled'}

@router.websocket("/ws/parser/{job_id}")
async def ws_parser(websocket: WebSocket, job_id: str, db: Database = Depends(get_database), job_manager: ParsingJobManager = Depends(get_parser_jobs)):
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

@router.post("/api/parser/item")
async def api_parse_item(req: ParseItemRequest, db: Database = Depends(get_database), browser_engine=Depends(get_browser_engine)):
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

