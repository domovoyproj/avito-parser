from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from auth_dependencies import require_admin
from database import Database
from dependencies import get_database
from config import config
from pydantic import BaseModel, Field

router = APIRouter(dependencies=[Depends(require_admin)])


class AlertPreferences(BaseModel):
    min_deal_score: int = Field(default=70, ge=0, le=100)
    notify_new: bool = True
    notify_drops: bool = True
    only_below_market: bool = False
    below_market_pct: int = Field(default=15, ge=0, le=100)
    delivery_only: bool = False
    quiet_hours_enabled: bool = False
    quiet_hours_start: int = Field(default=23, ge=0, le=23)
    quiet_hours_end: int = Field(default=7, ge=0, le=23)


@router.get('/api/notifications/preferences')
async def alert_preferences(db: Database = Depends(get_database)):
    return {'chats': [(await db.get_chat_settings(chat)).model_dump(mode='json')
                      for chat in sorted(set(config.telegram.admin_chat_ids))]}


@router.post('/api/notifications/preferences/{chat_id}')
async def save_alert_preferences(chat_id: int, preferences: AlertPreferences, db: Database = Depends(get_database)):
    if chat_id not in config.telegram.admin_chat_ids:
        raise HTTPException(404, 'Добавьте получателя в настройках Telegram')
    settings = await db.update_chat_settings(chat_id, **preferences.model_dump())
    return settings.model_dump(mode='json')


@router.get('/api/notifications')
async def notification_events(
    status: Optional[str] = Query(default=None, pattern='^(pending|sending|sent|failed|skipped)$'),
    limit: int = Query(default=50, ge=1, le=200),
    db: Database = Depends(get_database),
):
    data = await db.get_notification_events(status, limit)
    webhook = await db.get_webhook_settings()
    data['telegram_configured'] = bool(config.telegram.bot_token and config.telegram.admin_chat_ids)
    data['webhook_configured'] = bool(webhook.enabled and webhook.url)
    return data


@router.post('/api/notifications/{event_id}/retry')
async def retry_notification(event_id: int, db: Database = Depends(get_database)):
    if not await db.retry_notification(event_id):
        raise HTTPException(409, 'Повтор доступен только для уведомлений с ошибкой доставки')
    return {'status': 'pending'}
