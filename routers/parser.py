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
from dependencies import get_database, get_parser_jobs, get_http_engine, get_browser_engine, get_monitoring_service
router=APIRouter()
logger=logging.getLogger('AvitoParserAPI')

class ParseSearchRequest(BaseModel):
    url: str
    max_pages: int = Field(default=3, ge=1, le=50)
    engine: str = Field(default="playwright", description="playwright или http")
    save_to_db: bool = True
    headless: Optional[bool] = None

def job_timeout_seconds(max_pages: int) -> int:
    # Budget includes page loads and up to two five-minute manual checks.
    return min(3600, max(300, max_pages * 60 + 600))


@router.get("/api/parser/jobs/{job_id}")
async def parser_job_status(job_id: str, job_manager: ParsingJobManager = Depends(get_parser_jobs)):
    job = job_manager.active_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Задача не найдена: история хранится до перезапуска сервера")
    return job


@router.get("/api/parser/attention")
async def parser_attention(job_manager: ParsingJobManager = Depends(get_parser_jobs), monitor=Depends(get_monitoring_service), db: Database = Depends(get_database)):
    jobs = [{"job_id": job["job_id"], "url": job["url"], "status": job["status"],
             "message": job.get("access_message") or "Авито ограничил доступ. Требуется ручная проверка.",
             "captcha_count": job.get("captcha_count", 0)}
            for job in job_manager.active_jobs.values() if job["status"] in ("blocked", "waiting_human") and not job.get("attention_dismissed")]
    enabled_ids = {search.id for search in await db.get_searches(enabled_only=True)}
    return {"jobs": jobs, "searches": [alert for alert in getattr(monitor, "access_alerts", {}).values() if alert['search_id'] in enabled_ids]}


@router.post("/api/parser/jobs/{job_id}/dismiss")
async def dismiss_attention(job_id: str, job_manager: ParsingJobManager = Depends(get_parser_jobs)):
    job = job_manager.active_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    job["attention_dismissed"] = True
    return {"status": "dismissed"}


@router.post("/api/parser/start")
async def api_start_parse_search(req: ParseSearchRequest, db: Database = Depends(get_database), job_manager: ParsingJobManager = Depends(get_parser_jobs), http_engine=Depends(get_http_engine), browser_engine=Depends(get_browser_engine), monitor=Depends(get_monitoring_service)):
    if not avito_url(req.url):
        raise HTTPException(status_code=400, detail="Ссылка должна вести на avito.ru")
    if req.engine not in ('http', 'playwright'):
        raise HTTPException(status_code=422, detail='Неизвестный движок')
    job_id = 'job_' + secrets.token_hex(12)
    job = job_manager.create_job(job_id, req.url, req.max_pages, req.engine)
    job.update({"save_to_db": req.save_to_db, "headless": req.headless})
    collected = {}
    persisted = set()
    counts = {"new_items_count": 0, "price_dropped_count": 0}
    persistence_note = ("Уже сохранённые страницы остаются в каталоге." if req.save_to_db else
                        "Полученные объявления доступны в результатах этой задачи без сохранения в каталог.")

    async def collect(items):
        for item in items:
            collected[item.id] = item
        pending = [item for item in items if item.id not in persisted]
        if req.save_to_db and pending:
            saved = await db.save_items(pending)
            persisted.update(item.id for item in pending)
            counts["new_items_count"] += saved["new_count"]
            counts["price_dropped_count"] += saved["price_drop_count"]
        await job_manager.broadcast(job_id, "progress", {"items_found": len(collected), "saved_count": len(persisted), **counts})

    async def progress(page_num, max_p, total):
        await job_manager.broadcast(job_id, "progress", {
            "progress_pct": int(page_num / max_p * 100), "current_page": page_num, "items_found": total})
        await job_manager.broadcast(job_id, "log", f"Страница {page_num}/{max_p}: найдено {total} объявлений")

    async def access(data):
        await job_manager.broadcast(job_id, "access", data)
        if data.get("access_message"):
            await job_manager.broadcast(job_id, "log", data["access_message"])

    async def finish(outcome, errors=None):
        errors = errors or []
        if outcome in ('success', 'empty') and hasattr(monitor, 'clear_access_alert'):
            monitor.clear_access_alert(req.url)
        if outcome == "blocked" and not job.get("access_message"):
            await access({"status": "blocked", "access_kind": "blocked",
                          "access_message": "Авито ограничил доступ. Проверьте маршрут VPN и пройдите проверку в видимом браузере."})
        status = {"blocked": "blocked", "error": "error", "cancelled": "cancelled", "timeout": "error"}.get(outcome, "completed")
        await job_manager.broadcast(job_id, "complete", {
            "status": status, "outcome": outcome, "items": [item.model_dump(mode="json") for item in collected.values()],
            "total_found": len(collected), "saved_count": len(persisted), **counts, "errors": errors,
            "elapsed_sec": round(time.time() - job["start_time"], 2), "waiting_until": None,
            "progress_pct": 100 if outcome in ("success", "empty") else job["progress_pct"]})
        if persisted:
            try:
                await db.compute_and_update_market_averages()
            except Exception:
                logger.exception("Не удалось пересчитать рыночные средние после сбора")

    async def run_parser_task():
        await job_manager.broadcast(job_id, "progress", {"status": "running"})
        await job_manager.broadcast(job_id, "log", f"Начат сбор {req.max_pages} страниц. " + ("Результаты сохраняются после каждой страницы." if req.save_to_db else "Сохранение в каталог отключено."))
        if req.engine == "http":
            result = await http_engine.parse_search(req.url, max_pages=req.max_pages,
                                                  progress_callback=progress, items_callback=collect)
        else:
            engine = browser_engine if req.headless is None else type(browser_engine)(headless=req.headless)
            result = await engine.parse_search(req.url, max_pages=req.max_pages, progress_callback=progress,
                                               items_callback=collect, access_callback=access)
        await collect(result.items)
        for error in result.errors:
            await job_manager.broadcast(job_id, "log", error)
        await finish(result.outcome, result.errors)

    async def bounded_task():
        try:
            await asyncio.wait_for(run_parser_task(), timeout=job_timeout_seconds(req.max_pages))
        except asyncio.TimeoutError:
            await finish("timeout", ["Истекло время сбора. " + persistence_note + " Уменьшите глубину или проверьте доступ к Авито."])
        except asyncio.CancelledError:
            await finish("cancelled", ["Сбор остановлен. " + persistence_note])
            raise
        except Exception as exc:
            logger.exception("Ошибка задачи парсинга %s", job_id)
            await finish("error", [f"Не удалось завершить сбор: {type(exc).__name__}"])

    task = asyncio.create_task(bounded_task())
    job_manager.tasks[job_id] = task

    def finished(done):
        job_manager.tasks.pop(job_id, None)
        # Cancellation before the coroutine's first instruction is possible.
        if done.cancelled() and job["status"] not in ("cancelled", "completed", "blocked", "error"):
            job.update({"status": "cancelled", "outcome": "cancelled"})
        elif not done.cancelled() and done.exception():
            job.update({"status": "error", "outcome": "error"})
            job["errors"].append(type(done.exception()).__name__)
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


@router.post("/api/parser/jobs/{job_id}/retry-human")
async def retry_human(job_id: str, db: Database = Depends(get_database), job_manager: ParsingJobManager = Depends(get_parser_jobs), http_engine=Depends(get_http_engine), browser_engine=Depends(get_browser_engine), monitor=Depends(get_monitoring_service)):
    job = job_manager.active_jobs.get(job_id)
    if not job or job["status"] != "blocked":
        raise HTTPException(status_code=409, detail="Ручная проверка доступна для остановленного заблокированного сбора")
    existing = job_manager.active_jobs.get(job.get("human_retry_job"))
    if existing and existing["status"] in ("pending", "running", "waiting_human"):
        return {"status": "started", "job_id": existing["job_id"]}
    result = await api_start_parse_search(ParseSearchRequest(url=job["url"], max_pages=job["max_pages"],
                                                             engine="playwright", headless=False,
                                                             save_to_db=job.get("save_to_db", True)),
                                         db, job_manager, http_engine, browser_engine, monitor)
    job["human_retry_job"] = result["job_id"]
    job["attention_dismissed"] = True
    return result

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

