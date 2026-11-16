"""Private, linked-account CRM commands. No access from the legacy admin allowlist alone."""
import html
import shlex
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pydantic import ValidationError
from database import db
from crm_models import DealInput, ContactInput, TaskInput, CRMError, STAGES

router = Router(name='team_crm')


class CRMInput(StatesGroup):
    command = State()


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=value) for text, value in row] for row in rows])


MENU = keyboard([[('Сделки', 'crm:list:0'), ('Итоги', 'crm:summary')],
                 [('Новая сделка', 'crm:prompt:add'), ('Расход', 'crm:prompt:expense')],
                 [('Продажа', 'crm:prompt:sale'), ('Контакты', 'crm:contacts:0')],
                 [('Задачи', 'crm:tasks:0'), ('Новая задача', 'crm:prompt:task')],
                 [('Добавить контакт', 'crm:prompt:contact'), ('Помощь', 'crm:help')]])
PROMPTS = {
    'add': 'Введите: название | закупка в рублях или - | плановая цена или -',
    'expense': 'Введите: номер сделки | сумма в рублях | описание расхода',
    'sale': 'Введите: номер сделки | цена продажи в рублях',
    'contact': 'Введите: имя | телефон | Telegram | заметка (последние поля необязательны)',
    'task': 'Введите: задача | срок 2026-12-01T18:00+03:00 | номер сделки или - | ID ответственного или - (вы)',
    'set': 'Введите: номер сделки | поле | значение. Поля: title, stage, purchase, target, owner_id, supplier_id, buyer_id, notes, serial, listing_url, archived. Для очистки: -',
}
HELP = ('<b>Общая CRM команды</b>\nВсе суммы — рубли. Для отмены ввода: /cancel.\n'
        '/crm — меню\n/crm list — сделки; /crm deal 12 — карточка\n/crm add название | закупка | план\n'
        '/crm expense 12 | 500 | доставка\n/crm sale 12 | 18000\n/crm set 12 | stage | repair\n'
        '/crm set 12 | owner_id | 2\n/crm void 12 | 3 — отменить расход №3\n'
        '/crm contact имя | телефон | Telegram | заметка\n/crm contactedit 3 | имя | телефон | Telegram | заметка\n'
        '/crm task название | 2026-12-01T18:00+03:00 | сделка или - | сотрудник или -\n'
        '/crm taskedit 4 | название | 2026-12-01T18:00+03:00 | сделка или - | сотрудник или -\n'
        '/crm done 4 — завершить задачу; /crm reopen 4 — вернуть\n/crm team — сотрудники\n'
        '/crm archived — архив; /crm set 12 | archived | 0 — вернуть сделку\n'
        'Этапы: lead, purchased, repair, ready, listed, reserved, sold, cancelled.\n'
        'Продажа оформляется командой sale. Для отмены продажи: set 12 | stage | listed.')


def money(text):
    if text.strip() == '-':
        return None
    try:
        value = Decimal(text.replace(' ', '').replace(',', '.'))
        if not value.is_finite() or value < 0 or value > 1_000_000_000 or value * 100 != (value * 100).to_integral_value():
            raise ValueError
        return int(value * 100)
    except (InvalidOperation, ValueError):
        raise CRMError('Сумма должна быть неотрицательной, максимум два знака после запятой')


def rub(value):
    return '—' if value is None else f'{value / 100:,.2f} ₽'.replace(',', ' ')


async def identity(event):
    message = event.message if isinstance(event, types.CallbackQuery) else event
    sender = event.from_user
    if not message or message.chat.type != 'private' or not sender or sender.id != message.chat.id:
        raise CRMError('CRM доступна только в личном чате с ботом', 403)
    user = await db.crm_telegram_user(message.chat.id)
    if not user:
        raise CRMError('Сначала привяжите аккаунт: панель → Мои списки → Получить код привязки → /link код', 403)
    return user


async def record(kind, record_id, user_id):
    # Fetch by ID without depending on the first page of the list.
    import aiosqlite
    from repositories.crm import actor
    table = {'tasks': 'crm_tasks', 'contacts': 'crm_contacts'}[kind]
    async with db.connection() as c:
        c.row_factory = aiosqlite.Row
        await actor(c, user_id)
        row = await (await c.execute(f'SELECT * FROM {table} WHERE id=?', (record_id,))).fetchone()
        if not row:
            raise CRMError('Запись не найдена', 404)
        return dict(row)


async def execute(message, user, command, request_key):
    bits = command.strip().split(maxsplit=1)
    action, args = (bits[0].lower(), bits[1] if len(bits) > 1 else '') if bits else ('menu', '')
    parts = [p.strip() for p in args.split('|')]
    uid = user['id']
    if action in ('menu', 'help'):
        await message.answer(HELP if action == 'help' else 'CRM · общая база команды', parse_mode='HTML', reply_markup=MENU)
        return
    if action in ('list', 'archived'):
        offset = max(0, int(args or 0))
        result = await db.crm_deals(uid, archived=action == 'archived', offset=offset, limit=8)
        rows = [[(f"#{d['id']} · {d['title'][:35]} · {STAGES[d['stage']]}", f"crm:deal:{d['id']}")] for d in result['items']]
        if offset:
            rows.append([('← Назад', f'crm:{action}:{max(0, offset-8)}')])
        if offset + 8 < result['total']:
            rows.append([('Далее →', f'crm:{action}:{offset+8}')])
        rows.append([('Меню', 'crm:menu')])
        await message.answer(f"{'Архив' if action == 'archived' else 'Сделки'}: {result['total']}", reply_markup=keyboard(rows))
        return
    if action == 'deal':
        d = await db.crm_deal(uid, int(args))
        text = (f"<b>#{d['id']} · {html.escape(d['title'])}</b>\n{STAGES[d['stage']]}\n"
                f"Ответственный: {html.escape(d['owner_name'] or 'Не назначен')}\n"
                f"Закупка: {rub(d['purchase'])}\nРасходы: {rub(d['expenses'])}\nПлан: {rub(d['target'])}\n"
                f"Продажа: {rub(d['sale'])}\nПрибыль: {rub(d['profit'])}\n"
                f"{html.escape(d['notes'][:1000])}")
        text += '\n' + '\n'.join(f"Расход #{e['id']}: {rub(e['amount'])} · {html.escape(e['note'][:60])}" for e in d['expense_rows'][:8] if not e['voided'])
        text += '\nИстория:\n' + '\n'.join(html.escape(h['username'] + ': ' + h['action']) for h in d['history'][:4])
        await message.answer(text, parse_mode='HTML', reply_markup=keyboard([[('Изменить', 'crm:prompt:set'), ('Меню', 'crm:menu')]]))
        return
    if action in ('summary', 'team'):
        summary = await db.crm_overview(uid)
        text = ('\n'.join(f"#{u['id']} · {html.escape(u['username'])} · {u['role']}" for u in summary['users']) if action == 'team' else
                f"В товаре: {rub(summary['stock_cost'])}\nПродаж: {summary['sold_count']}\nВыручка: {rub(summary['revenue'])}\nПрибыль: {rub(summary['profit'])}\nПросроченных задач: {summary['overdue']}")
        await message.answer(text or 'Нет данных', parse_mode='HTML', reply_markup=MENU)
        return
    if action in ('contacts', 'tasks'):
        offset = max(0, int(args or 0))
        result = await db.crm_records(uid, action, offset, 8)
        if action == 'contacts':
            lines = [f"#{r['id']} · {html.escape(r['name'])}\n{html.escape(r['phone'])} {html.escape(r['telegram'])}" for r in result['items']]
        else:
            lines = [f"{'✓' if r['done'] else '○'} #{r['id']} · {html.escape(r['title'])}\n{datetime.fromtimestamp(r['due_at'], timezone.utc).isoformat()} · сотрудник #{r['owner_id']}" for r in result['items']]
        rows = [[('Меню', 'crm:menu')]]
        if offset: rows.append([('← Назад', f'crm:{action}:{max(0, offset-8)}')])
        if offset+8 < result['total']: rows.append([('Далее →', f'crm:{action}:{offset+8}')])
        await message.answer('\n\n'.join(lines) or 'Список пуст', parse_mode='HTML', reply_markup=keyboard(rows))
        return
    if user['role'] not in ('admin', 'operator'):
        raise CRMError('У вас доступ только для просмотра', 403)
    if action == 'add' and len(parts) == 3:
        purchase = money(parts[1])
        saved = await db.crm_save_deal(uid, {'title': parts[0], 'purchase': purchase, 'target': money(parts[2]), 'owner_id': uid, 'stage': 'purchased' if purchase is not None else 'lead'})
        await message.answer(f"Создана сделка №{saved['id']}", reply_markup=MENU)
        return
    if action in ('expense', 'void') and len(parts) == (3 if action == 'expense' else 2):
        if action == 'expense':
            await db.crm_add_expense(uid, int(parts[0]), {'amount': money(parts[1]), 'note': parts[2], 'request_key': request_key})
        else:
            await db.crm_void_expense(uid, int(parts[0]), int(parts[1]))
    elif action in ('sale', 'set') and len(parts) == (2 if action == 'sale' else 3):
        d = await db.crm_deal(uid, int(parts[0]))
        data = DealInput.model_validate({key: d[key] for key in DealInput.model_fields}).model_dump()
        if action == 'sale':
            data.update(stage='sold', sale=money(parts[1]))
        else:
            key, value = parts[1], parts[2]
            allowed = {'title','stage','purchase','target','owner_id','supplier_id','buyer_id','notes','serial','listing_url','source_url','archived'}
            if key not in allowed:
                raise CRMError('Неизвестное поле. /crm help')
            if key in ('purchase', 'target'): value = money(value)
            elif key.endswith('_id'): value = None if value == '-' else int(value)
            elif key == 'archived':
                if value not in ('0','1'): raise CRMError('Архив: 0 или 1')
                value = value == '1'
            else: value = '' if value == '-' else value
            data[key] = value
            if key == 'stage' and value != 'sold': data['sale'] = None
        await db.crm_save_deal(uid, data, d['id'], d['version'])
    elif action in ('contact','contactedit'):
        old = await record('contacts', int(parts.pop(0)), uid) if action == 'contactedit' else None
        if not 1 <= len(parts) <= 4: raise CRMError('Формат: имя | телефон | Telegram | заметка')
        data = dict(zip(('name','phone','telegram','notes'), parts))
        await db.crm_save_record(uid, 'contacts', data, old['id'] if old else None, old['version'] if old else None)
    elif action in ('task','taskedit'):
        old = await record('tasks', int(parts.pop(0)), uid) if action == 'taskedit' else None
        if len(parts) != 4: raise CRMError(PROMPTS['task'])
        data = {'title':parts[0], 'due_at':parts[1], 'deal_id':None if parts[2]=='-' else int(parts[2]), 'owner_id':uid if parts[3]=='-' else int(parts[3]), 'done':bool(old['done']) if old else False}
        await db.crm_save_record(uid, 'tasks', data, old['id'] if old else None, old['version'] if old else None)
    elif action in ('done','reopen'):
        old = await record('tasks', int(args), uid)
        data = {key:old[key] for key in TaskInput.model_fields}
        data['due_at'] = datetime.fromtimestamp(old['due_at'], timezone.utc)
        data['done'] = action == 'done'
        await db.crm_save_record(uid, 'tasks', data, old['id'], old['version'])
    else:
        raise CRMError('Проверьте формат команды: /crm help')
    await message.answer('Сохранено в общей CRM', reply_markup=MENU)


async def safe_execute(message, user, command):
    try:
        await execute(message, user, command, f'tg:{message.chat.id}:{message.message_id}')
        return True
    except (CRMError, ValueError, ValidationError, IndexError) as exc:
        text = str(exc) if isinstance(exc, CRMError) else 'Проверьте поля, номера и суммы. /crm help'
        await message.answer(text, parse_mode=None)
        return False


@router.message(Command('crm'))
async def command(message: types.Message, state: FSMContext):
    await state.clear()
    try:
        user = await identity(message)
    except CRMError as exc:
        await message.answer(str(exc)); return
    bits = (message.text or '').split(maxsplit=1)
    await safe_execute(message, user, bits[1] if len(bits)>1 else '')


@router.callback_query(F.data.startswith('crm:'))
async def callback(event: types.CallbackQuery, state: FSMContext):
    try:
        user = await identity(event)
    except CRMError as exc:
        await event.answer(str(exc), show_alert=True); return
    await event.answer()
    await state.clear()
    parts = event.data.split(':')
    if parts[1] == 'prompt':
        action = parts[2]
        if action not in PROMPTS or user['role'] not in ('admin', 'operator'):
            await event.message.answer('У вас доступ только для просмотра'); return
        await state.set_state(CRMInput.command)
        await state.update_data(action=action)
        await event.message.answer(PROMPTS[action] + '\nОтменить: /cancel')
    else:
        await safe_execute(event.message, user, ' '.join(parts[1:]))


@router.message(CRMInput.command)
async def input_command(message: types.Message, state: FSMContext):
    if (message.text or '').startswith('/'):
        await state.clear()
        await message.answer('Ввод отменён. /crm — меню'); return
    try:
        user = await identity(message)
    except CRMError as exc:
        await state.clear(); await message.answer(str(exc)); return
    data = await state.get_data()
    if await safe_execute(message, user, data['action']+' '+(message.text or '')):
        await state.clear()
