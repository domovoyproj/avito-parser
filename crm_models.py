"""Shared validation for the team CRM, web API and Telegram."""
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

STAGES = {'lead': 'К покупке', 'purchased': 'Куплено', 'repair': 'Ремонт',
          'ready': 'Готово к продаже', 'listed': 'На продаже', 'reserved': 'Бронь',
          'sold': 'Продано', 'cancelled': 'Отменено'}
Stage = Literal['lead', 'purchased', 'repair', 'ready', 'listed', 'reserved', 'sold', 'cancelled']
Money = int


class CRMModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class DealInput(CRMModel):
    title: str = Field(min_length=1, max_length=200)
    stage: Stage = 'lead'
    purchase: Money | None = Field(default=None, ge=0, le=100_000_000_000, strict=True)
    target: Money | None = Field(default=None, ge=0, le=100_000_000_000, strict=True)
    sale: Money | None = Field(default=None, ge=0, le=100_000_000_000, strict=True)
    owner_id: int | None = Field(default=None, gt=0)
    supplier_id: int | None = Field(default=None, gt=0)
    buyer_id: int | None = Field(default=None, gt=0)
    source_url: str = Field(default='', max_length=2000)
    listing_url: str = Field(default='', max_length=2000)
    notes: str = Field(default='', max_length=5000)
    serial: str = Field(default='', max_length=200)
    item_id: str | None = Field(default=None, max_length=100)
    archived: bool = False

    @field_validator('source_url', 'listing_url')
    @classmethod
    def safe_url(cls, value):
        from urllib.parse import urlsplit
        if value:
            url = urlsplit(value)
            if url.scheme not in ('https', 'http') or not url.hostname or url.username or url.password:
                raise ValueError('Нужна ссылка http(s) без пароля')
        return value


class DealUpdate(DealInput):
    version: int = Field(ge=1)


class ExpenseInput(CRMModel):
    amount: Money = Field(gt=0, le=100_000_000_000, strict=True)
    category: Literal['repair', 'delivery', 'commission', 'other'] = 'other'
    note: str = Field(min_length=1, max_length=500)
    request_key: str = Field(min_length=1, max_length=100)


class ContactInput(CRMModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(default='', max_length=100)
    telegram: str = Field(default='', max_length=100)
    notes: str = Field(default='', max_length=3000)


class TaskInput(CRMModel):
    title: str = Field(min_length=1, max_length=300)
    deal_id: int | None = Field(default=None, gt=0)
    owner_id: int = Field(gt=0)
    due_at: datetime
    done: bool = False

    @field_validator('due_at')
    @classmethod
    def utc_due(cls, value):
        if value.tzinfo is None:
            raise ValueError('Укажите часовой пояс срока')
        return value.astimezone(timezone.utc)


class CRMError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status
