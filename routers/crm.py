"""Team CRM endpoints. Monetary fields are integer kopecks."""
import csv
import io
from fastapi import APIRouter, Depends, Query, Response
from auth_dependencies import require_auth
from dependencies import get_database
from crm_models import DealInput, DealUpdate, ExpenseInput, ContactInput, TaskInput, CRMModel
from pydantic import Field

router = APIRouter(prefix='/api/crm', tags=['CRM'])


class ContactUpdate(ContactInput):
    version: int = Field(ge=1)


class TaskUpdate(TaskInput):
    version: int = Field(ge=1)


@router.get('/overview')
async def overview(db=Depends(get_database), user=Depends(require_auth)):
    return await db.crm_overview(user.id)


@router.get('/deals')
async def deals(query: str = Query('', max_length=200), stage: str = '', owner_id: int | None = None,
                archived: bool = False, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                db=Depends(get_database), user=Depends(require_auth)):
    return await db.crm_deals(user.id, query, stage, owner_id, archived, offset, limit)


@router.get('/export')
async def export(db=Depends(get_database), user=Depends(require_auth)):
    output = io.StringIO(newline='')
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['ID', 'Товар', 'Этап', 'Ответственный', 'Закупка, руб.', 'Расходы, руб.', 'Продажа, руб.', 'Прибыль, руб.', 'Архив'])
    # A read transaction yields one consistent snapshot, including archived sales.
    from repositories.crm import DEAL_SELECT, deal_dict, actor
    import aiosqlite
    async with db.connection() as c:
        c.row_factory = aiosqlite.Row
        await c.execute('BEGIN')
        await actor(c, user.id)
        cursor = await c.execute(DEAL_SELECT + ' ORDER BY d.id')
        async for row in cursor:
            d = deal_dict(row)
            def safe(value):
                text = str(value or '')
                return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else text
            def money(value):
                return '' if value is None else f'{value / 100:.2f}'
            writer.writerow([d['id'], safe(d['title']), d['stage'], safe(d['owner_name']), money(d['purchase']),
                             money(d['expenses']), money(d['sale']), money(d['profit']), bool(d['archived'])])
    return Response('\ufeff'+output.getvalue(), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="crm.csv"'})


@router.post('/deals')
async def create_deal(payload: DealInput, db=Depends(get_database), user=Depends(require_auth)):
    return await db.crm_save_deal(user.id, payload.model_dump())


@router.get('/deals/{deal_id}')
async def deal(deal_id: int, db=Depends(get_database), user=Depends(require_auth)):
    return await db.crm_deal(user.id, deal_id)


@router.put('/deals/{deal_id}')
async def update_deal(deal_id: int, payload: DealUpdate, db=Depends(get_database), user=Depends(require_auth)):
    return await db.crm_save_deal(user.id, payload.model_dump(exclude={'version'}), deal_id, payload.version)


@router.post('/deals/{deal_id}/expenses')
async def expense(deal_id: int, payload: ExpenseInput, db=Depends(get_database), user=Depends(require_auth)):
    return await db.crm_add_expense(user.id, deal_id, payload.model_dump())


@router.delete('/deals/{deal_id}/expenses/{expense_id}')
async def void_expense(deal_id: int, expense_id: int, db=Depends(get_database), user=Depends(require_auth)):
    await db.crm_void_expense(user.id, deal_id, expense_id)
    return {'ok': True}


@router.get('/contacts')
async def contacts(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500), db=Depends(get_database), user=Depends(require_auth)):
    return await db.crm_records(user.id, 'contacts', offset, limit)


@router.post('/contacts')
async def create_contact(payload: ContactInput, db=Depends(get_database), user=Depends(require_auth)):
    return await db.crm_save_record(user.id, 'contacts', payload.model_dump())


@router.put('/contacts/{record_id}')
async def update_contact(record_id: int, payload: ContactUpdate, db=Depends(get_database), user=Depends(require_auth)):
    return await db.crm_save_record(user.id, 'contacts', payload.model_dump(exclude={'version'}), record_id, payload.version)


@router.get('/tasks')
async def tasks(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500), db=Depends(get_database), user=Depends(require_auth)):
    return await db.crm_records(user.id, 'tasks', offset, limit)


@router.post('/tasks')
async def create_task(payload: TaskInput, db=Depends(get_database), user=Depends(require_auth)):
    return await db.crm_save_record(user.id, 'tasks', payload.model_dump())


@router.put('/tasks/{record_id}')
async def update_task(record_id: int, payload: TaskUpdate, db=Depends(get_database), user=Depends(require_auth)):
    return await db.crm_save_record(user.id, 'tasks', payload.model_dump(exclude={'version'}), record_id, payload.version)
