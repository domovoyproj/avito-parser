"""Authenticated personal watchlists and Telegram linking."""

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth_dependencies import require_auth
from database import Database
from dependencies import get_database
from models import User


router = APIRouter()


class WatchlistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class WatchlistUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    alerts_paused: Optional[bool] = None


class WatchItemInput(BaseModel):
    target_price: Optional[int] = Field(default=None, ge=0, le=1000000000)
    note: str = Field(default="", max_length=1000)
    alerts_paused: bool = False


@router.get("/api/watchlists")
async def list_watchlists(db: Database = Depends(get_database), user: User = Depends(require_auth)):
    return await db.list_watchlists(user.id)


@router.post("/api/watchlists")
async def create_watchlist(payload: WatchlistCreate, db: Database = Depends(get_database), user: User = Depends(require_auth)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(422, "Название списка не может быть пустым")
    await db.default_watchlist_id(user.id)
    try:
        watchlist_id = await db.create_watchlist(user.id, name)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Список с таким названием уже существует")
    return {"id": watchlist_id}


@router.patch("/api/watchlists/{watchlist_id}")
async def update_watchlist(watchlist_id: int, payload: WatchlistUpdate, db: Database = Depends(get_database), user: User = Depends(require_auth)):
    name = payload.name.strip() if payload.name is not None else None
    if name == "":
        raise HTTPException(422, "Название списка не может быть пустым")
    try:
        updated = await db.update_watchlist(user.id, watchlist_id, name=name, alerts_paused=payload.alerts_paused)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Список с таким названием уже существует")
    if not updated:
        raise HTTPException(404, "Список не найден")
    return {"status": "success"}


@router.delete("/api/watchlists/{watchlist_id}")
async def delete_watchlist(watchlist_id: int, db: Database = Depends(get_database), user: User = Depends(require_auth)):
    if not await db.delete_watchlist(user.id, watchlist_id):
        raise HTTPException(404, "Список не найден или является основным")
    return {"status": "success"}


@router.get("/api/watchlists/{watchlist_id}/items")
async def list_watchlist_items(watchlist_id: int, db: Database = Depends(get_database), user: User = Depends(require_auth)):
    items = await db.list_watchlist_items(user.id, watchlist_id)
    if items is None:
        raise HTTPException(404, "Список не найден")
    return items


@router.put("/api/watchlists/{watchlist_id}/items/{item_id}")
async def put_watchlist_item(watchlist_id: int, item_id: str, payload: WatchItemInput, db: Database = Depends(get_database), user: User = Depends(require_auth)):
    ok = await db.set_watchlist_item(user.id, watchlist_id, item_id, **payload.model_dump())
    if not ok:
        raise HTTPException(404, "Список или объявление не найдены")
    return {"status": "success"}


@router.delete("/api/watchlists/{watchlist_id}/items/{item_id}")
async def delete_watchlist_item(watchlist_id: int, item_id: str, db: Database = Depends(get_database), user: User = Depends(require_auth)):
    if not await db.remove_watchlist_item(user.id, watchlist_id, item_id):
        raise HTTPException(404, "Запись не найдена")
    return {"status": "success"}


@router.get("/api/me/telegram-link")
async def get_telegram_links(db: Database = Depends(get_database), user: User = Depends(require_auth)):
    return {"chat_ids": await db.list_telegram_chats(user.id)}


@router.post("/api/me/telegram-link")
async def create_telegram_link(db: Database = Depends(get_database), user: User = Depends(require_auth)):
    code = await db.issue_telegram_link(user.id)
    return {"code": code, "expires_in_seconds": 600, "instruction": "Отправьте боту в личном чате: /link " + code}


@router.delete("/api/me/telegram-link/{chat_id}")
async def delete_telegram_link(chat_id: int, db: Database = Depends(get_database), user: User = Depends(require_auth)):
    if not await db.unlink_telegram_chat(user.id, chat_id):
        raise HTTPException(404, "Чат не найден")
    return {"status": "success"}
