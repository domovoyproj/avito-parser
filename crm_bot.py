"""Private, linked-account CRM commands. No access from the legacy admin allowlist alone."""
import html
import secrets
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
from crm_links import listing_reference, message_links
from security import avito_url

router = Router(name='team_crm')


class CRMInput(StatesGroup):
    command = State()
    import_link = State()
    import_name = State()
    import_price = State()
    import_preview = State()


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=value) for text, value in row] for row in rows])


MENU = keyboard([[('Сделки', 'crm:list:0'), ('Итоги', 'crm:summary')],
                 [('Перенести объявление', 'crm:prompt:import')],
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
        'Перенос: кнопка «В CRM» под уведомлением, ссылка на Авито или пересылка сообщения со ссылкой.\n'
        '/crm import ссылка — перенести объявление\n'
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
        text = (f"<b>#{d['id']} · {html.escape(d['title'])}</b>\n{STAGES[d['stage']]}{' · В архиве' if d['archived'] else ''}\n"
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
    if len(bits)>1 and (bits[1].split(maxsplit=1)[0] == 'import' or listing_reference(bits[1])):
        value = bits[1].removeprefix('import').strip()
        if not value:
            await state.set_state(CRMInput.import_link)
            await message.answer('Пришлите ссылку на объявление Авито или перешлите сообщение с ней. Отмена: /cancel')
        else:
            await begin_import(message, state, user, source_url=value)
        return
    await safe_execute(message, user, bits[1] if len(bits)>1 else '')


@router.callback_query(F.data.startswith('crm:'))
async def callback(event: types.CallbackQuery, state: FSMContext):
    try:
        user = await identity(event)
    except CRMError as exc:
        await event.answer(str(exc), show_alert=True); return
    await event.answer()
    parts = event.data.split(':')
    if parts[1] == 'import' and len(parts) == 3:
        await begin_import(event.message, state, user, item_id=parts[2])
        return
    if parts[1] in ('saveimport', 'buyimport', 'priceimport', 'cancelimport'):
        await import_action(event, state, user, parts)
        return
    await state.clear()
    if parts[1] == 'prompt':
        action = parts[2]
        if action == 'import':
            if user['role'] not in ('admin', 'operator'):
                await event.message.answer('У вас доступ только для просмотра'); return
            await state.set_state(CRMInput.import_link)
            await event.message.answer('Пришлите ссылку на объявление Авито или перешлите сообщение с ней. Отмена: /cancel')
            return
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


async def begin_import(message, state, user, item_id=None, source_url=''):
    ref = listing_reference(source_url) if source_url else None
    if source_url and not ref:
        await message.answer('Нужна прямая ссылка на объявление Авито с номером в конце, не ссылка на поиск.'); return
    if ref:
        item_id, source_url = ref
    item = await db.get_item_by_id(item_id) if item_id else None
    if item:
        source_url = item.url
    elif not source_url:
        source_url = next((url for url in message_links(message) if listing_reference(url)[0] == item_id), '')
    existing = await db.crm_find_import(user['id'], item_id, source_url)
    if existing:
        await state.clear()
        await message.answer(f'Объявление уже в общей CRM — сделка №{existing}.')
        await safe_execute(message, user, f'deal {existing}')
        return
    if user['role'] not in ('admin', 'operator'):
        await message.answer('У вас доступ только для просмотра'); return
    if not source_url or not avito_url(source_url):
        await message.answer('Объявление больше не найдено в каталоге. Пришлите его ссылку, чтобы заполнить карточку.'); return
    await state.clear()
    draft = {'title': (item.title or '')[:200] if item else '', 'source_url': source_url,
             'item_id': item.id if item else None, 'purchase': item.price*100 if item and item.price is not None else None,
             'notes': (item.description or '')[:5000] if item else '', 'stage': 'lead', 'owner_id': user['id']}
    await state.update_data(import_draft=draft, import_nonce=secrets.token_hex(6))
    if not draft['title']:
        await state.set_state(CRMInput.import_name)
        await message.answer('Этого объявления пока нет в каталоге. Ссылка сохранена в черновике. Напишите название товара. Отмена: /cancel')
    else:
        await show_import_preview(message, state)


async def show_import_preview(message, state):
    data = await state.get_data()
    draft, nonce = data['import_draft'], secrets.token_hex(6)
    await state.update_data(import_nonce=nonce)
    await state.set_state(CRMInput.import_preview)
    purchased = draft['stage'] == 'purchased'
    rows = [[('Сохранить покупку' if purchased else 'Добавить к покупке', f'crm:saveimport:{nonce}')]]
    if not purchased:
        rows.append([('Уже куплено', f'crm:buyimport:{nonce}')])
    rows += [[('Изменить цену', f'crm:priceimport:{nonce}'), ('Отмена', f'crm:cancelimport:{nonce}')]]
    await message.answer('<b>Перенос в CRM</b>\n' + html.escape(draft['title']) +
        f"\n{'Фактическая закупка' if purchased else 'Цена объявления'}: {rub(draft['purchase'])}" +
        f"\nЭтап: {STAGES[draft['stage']]}\n" +
        ('Название, цена, описание и ссылка взяты из сохранённого каталога.\n' if draft['item_id'] else 'Данные заполнены вручную, ссылка сохранится в сделке.\n') +
        'Ответственный — вы. Сделка будет общей для команды.\n' +
        '<a href="' + html.escape(draft['source_url'], quote=True) + '">Объявление на Авито</a>',
        parse_mode='HTML', reply_markup=keyboard(rows), disable_web_page_preview=True)


async def import_action(event, state, user, parts):
    data = await state.get_data()
    nonce = parts[2] if len(parts) == 3 else ''
    if data.get('import_completed_nonce') == nonce and data.get('import_completed_id'):
        await safe_execute(event.message, user, f"deal {data['import_completed_id']}")
        return
    if not nonce or data.get('import_nonce') != nonce or not data.get('import_draft'):
        await event.message.answer('Этот черновик больше не активен. Нажмите «В CRM» у объявления или отправьте ссылку заново.'); return
    if parts[1] == 'cancelimport':
        await state.clear()
        await event.message.answer('Перенос отменён. Сделка не создана.'); return
    if user['role'] not in ('admin', 'operator'):
        await event.message.answer('У вас доступ только для просмотра'); return
    if parts[1] == 'saveimport' and await state.get_state() != CRMInput.import_preview.state:
        await event.message.answer('Сначала введите цену и подтвердите обновлённую карточку.'); return
    if parts[1] in ('buyimport', 'priceimport'):
        if parts[1] == 'buyimport':
            draft = data['import_draft'] | {'stage': 'purchased'}
            await state.update_data(import_draft=draft)
        await state.set_state(CRMInput.import_price)
        await event.message.answer('Введите фактическую цену закупки в рублях.' if parts[1] == 'buyimport' else 'Введите цену в рублях. Для неизвестной цены на этапе «К покупке» отправьте -.')
        return
    try:
        saved = await db.crm_save_deal(user['id'], data['import_draft'], reuse_source=True)
    except (CRMError, ValidationError) as exc:
        await event.message.answer(str(exc) if isinstance(exc, CRMError) else 'Проверьте название и сумму. Перешлите ссылку заново.'); return
    await state.set_state(None)
    await state.set_data({'import_completed_nonce': nonce, 'import_completed_id': saved['id']})
    await event.message.answer(f"Готово: сделка №{saved['id']} в общей CRM. Если её уже добавил коллега, открыта существующая карточка.")
    await safe_execute(event.message, user, f"deal {saved['id']}")


@router.message(CRMInput.import_link, F.text | F.caption)
@router.message(CRMInput.import_name, F.text | F.caption)
@router.message(CRMInput.import_price, F.text | F.caption)
@router.message(CRMInput.import_preview, F.text | F.caption)
async def import_input(message: types.Message, state: FSMContext):
    try:
        user = await identity(message)
    except CRMError as exc:
        await state.clear(); await message.answer(str(exc)); return
    text = (message.text or message.caption or '').strip()
    if text.startswith('/'):
        await state.clear(); await message.answer('Ввод отменён. /crm — меню'); return
    links = message_links(message)
    if links:
        await receive_links(message, state, user, links); return
    data = await state.get_data()
    current = await state.get_state()
    if current == CRMInput.import_link.state:
        await message.answer('Пришлите прямую ссылку на объявление Авито. Для отмены: /cancel'); return
    if user['role'] not in ('admin','operator'):
        await state.clear(); await message.answer('У вас доступ только для просмотра'); return
    draft = data.get('import_draft')
    if not draft:
        await state.clear(); await message.answer('Пришлите ссылку заново.'); return
    if current == CRMInput.import_name.state:
        if not 1 <= len(text) <= 200:
            await message.answer('Название должно содержать от 1 до 200 символов.'); return
        draft['title'] = text
        await state.update_data(import_draft=draft)
        await state.set_state(CRMInput.import_price)
        await message.answer('Укажите цену объявления в рублях. Если неизвестна — отправьте -.'); return
    if current == CRMInput.import_price.state:
        try:
            value = money(text)
            if value is None and draft['stage'] == 'purchased':
                raise CRMError('Для купленного товара нужна фактическая цена закупки')
        except CRMError as exc:
            await message.answer(str(exc)); return
        draft['purchase'] = value
        await state.update_data(import_draft=draft)
        await show_import_preview(message, state)
        return
    await message.answer('Выберите действие под карточкой выше или отправьте /cancel.')


async def receive_links(message, state, user, links):
    if len(links) != 1:
        await message.answer('В сообщении несколько объявлений. Пришлите одно, чтобы не перепутать товары.'); return
    await begin_import(message, state, user, source_url=links[0])


@router.message(lambda message: not (message.text or '').startswith('/') and bool(message_links(message)))
async def import_shared_link(message: types.Message, state: FSMContext):
    try:
        user = await identity(message)
    except CRMError as exc:
        await message.answer(str(exc)); return
    await receive_links(message, state, user, message_links(message))
