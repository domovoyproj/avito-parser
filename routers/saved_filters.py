"""Private named catalog filters. Shared URLs carry filter values, not access to saved records."""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Literal, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth_dependencies import require_auth
from database import Database
from dependencies import get_database
from models import User


router = APIRouter()


class CatalogFilterSet(BaseModel):
    query: str = Field(default="", max_length=200)
    search_query_id: Optional[str] = Field(default=None, pattern=r"^(?:-1|[1-9][0-9]*)$")
    min_price: Optional[int] = Field(default=None, ge=0)
    max_price: Optional[int] = Field(default=None, ge=0)
    with_discount_only: bool = False
    with_delivery_only: bool = False
    favorites_only: bool = False
    hide_reserved: bool = False
    deal_grade: Optional[Literal["GEM", "HOT", "FAIR", "CAUTION"]] = None
    sort_by: Literal["relevance", "deal_score_desc", "newest", "oldest", "price_asc", "price_desc", "discount_desc", "title"] = "relevance"


class SavedFilterInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    filters: CatalogFilterSet


@router.get("/api/saved-filters")
async def list_saved_filters(db: Database = Depends(get_database), user: User = Depends(require_auth)):
    async with db.connection() as connection:
        connection.row_factory = aiosqlite.Row
        rows = await (await connection.execute(
            "SELECT id,name,filters_json,created_at FROM saved_catalog_filters WHERE user_id=? ORDER BY name",
            (user.id,),
        )).fetchall()
    return [{"id": row["id"], "name": row["name"], "filters": json.loads(row["filters_json"]),
             "created_at": row["created_at"]} for row in rows]


@router.post("/api/saved-filters")
async def save_filter(payload: SavedFilterInput, db: Database = Depends(get_database),
                      user: User = Depends(require_auth)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(422, "Укажите название фильтра")
    async with db.connection() as connection:
        try:
            cursor = await connection.execute(
                "INSERT INTO saved_catalog_filters(user_id,name,filters_json,created_at) VALUES(?,?,?,?)",
                (user.id, name, payload.filters.model_dump_json(), datetime.now(timezone.utc).isoformat()),
            )
            await connection.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "Фильтр с таким названием уже существует") from exc
    return {"id": cursor.lastrowid, "name": name}


@router.delete("/api/saved-filters/{filter_id}")
async def delete_filter(filter_id: int, db: Database = Depends(get_database),
                        user: User = Depends(require_auth)):
    async with db.connection() as connection:
        cursor = await connection.execute(
            "DELETE FROM saved_catalog_filters WHERE id=? AND user_id=?", (filter_id, user.id))
        await connection.commit()
    if not cursor.rowcount:
        raise HTTPException(404, "Фильтр не найден")
    return {"deleted": True}
