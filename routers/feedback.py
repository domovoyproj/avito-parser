"""Authenticated scoring feedback and aggregate quality report."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth_dependencies import require_auth
from database import Database
from dependencies import get_database
from models import User
from repositories.feedback import LABELS


router = APIRouter()


class FeedbackInput(BaseModel):
    label: str
    note: str = Field(default="", max_length=1000)


@router.post("/api/items/{item_id}/feedback")
async def post_feedback(item_id: str, payload: FeedbackInput, db: Database = Depends(get_database),
                        user: User = Depends(require_auth)):
    if payload.label not in LABELS:
        raise HTTPException(422, "Неизвестная метка")
    result = await db.record_item_feedback(item_id, user.id, payload.label, payload.note)
    if result is None:
        raise HTTPException(404, "Объявление не найдено")
    return result


@router.get("/api/items/{item_id}/feedback")
async def get_feedback(item_id: str, db: Database = Depends(get_database),
                       user: User = Depends(require_auth)):
    if await db.get_item_by_id(item_id) is None:
        raise HTTPException(404, "Объявление не найдено")
    return {"history": await db.item_feedback_history(item_id, user.id)}


@router.get("/api/feedback/report")
async def feedback_report(category: str | None = None, search_query_id: int | None = None,
                          score_version: str | None = None, db: Database = Depends(get_database),
                          user: User = Depends(require_auth)):
    return await db.feedback_report(category, search_query_id, score_version)
