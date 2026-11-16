"""Shared team records. Every mutation checks the active actor in its transaction."""
import json
import time
from datetime import datetime, timezone
import aiosqlite
from crm_models import DealInput, ExpenseInput, ContactInput, TaskInput, CRMError, STAGES
from crm_links import listing_reference


async def init_crm(connection):
    statements = [
        '''CREATE TABLE IF NOT EXISTS crm_contacts (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '',
            telegram TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1)''',
        '''CREATE TABLE IF NOT EXISTS crm_deals (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'lead',
            purchase INTEGER, target INTEGER, sale INTEGER,
            owner_id INTEGER REFERENCES users(id), supplier_id INTEGER REFERENCES crm_contacts(id),
            buyer_id INTEGER REFERENCES crm_contacts(id), source_url TEXT NOT NULL DEFAULT '',
            listing_url TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', serial TEXT NOT NULL DEFAULT '',
            item_id TEXT, archived INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL, updated_at REAL NOT NULL, sold_at REAL)''',
        '''CREATE TABLE IF NOT EXISTS crm_expenses (
            id INTEGER PRIMARY KEY, deal_id INTEGER NOT NULL REFERENCES crm_deals(id),
            amount INTEGER NOT NULL CHECK(amount>0), category TEXT NOT NULL, note TEXT NOT NULL,
            actor_id INTEGER NOT NULL REFERENCES users(id), created_at REAL NOT NULL,
            voided INTEGER NOT NULL DEFAULT 0, request_key TEXT NOT NULL UNIQUE)''',
        '''CREATE TABLE IF NOT EXISTS crm_tasks (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL, deal_id INTEGER REFERENCES crm_deals(id),
            owner_id INTEGER NOT NULL REFERENCES users(id), due_at REAL NOT NULL, done INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS crm_history (
            id INTEGER PRIMARY KEY, entity TEXT NOT NULL, entity_id INTEGER NOT NULL,
            actor_id INTEGER NOT NULL REFERENCES users(id), action TEXT NOT NULL,
            detail TEXT NOT NULL, created_at REAL NOT NULL)''',
        'CREATE INDEX IF NOT EXISTS idx_crm_deals_stage ON crm_deals(archived,stage,updated_at)',
        'CREATE INDEX IF NOT EXISTS idx_crm_tasks_due ON crm_tasks(done,due_at)',
        'CREATE INDEX IF NOT EXISTS idx_crm_history ON crm_history(entity,entity_id,id)',
        'CREATE INDEX IF NOT EXISTS idx_crm_expenses_deal ON crm_expenses(deal_id,voided)',
    ]
    for statement in statements:
        await connection.execute(statement)


async def actor(connection, user_id, write=False):
    row = await (await connection.execute('SELECT id,username,role FROM users WHERE id=? AND is_active=1', (user_id,))).fetchone()
    if not row:
        raise CRMError('Аккаунт недоступен', 403)
    if write and row['role'] not in ('admin', 'operator'):
        raise CRMError('У вас доступ только для просмотра', 403)
    return dict(row)


async def history(connection, entity, entity_id, user_id, action, detail):
    await connection.execute('INSERT INTO crm_history(entity,entity_id,actor_id,action,detail,created_at) VALUES(?,?,?,?,?,?)',
                             (entity, entity_id, user_id, action, json.dumps(detail, ensure_ascii=False), time.time()))


async def reference(connection, table, value):
    if value is None:
        return
    extra = ' AND is_active=1' if table == 'users' else ''
    if not await (await connection.execute(f'SELECT id FROM {table} WHERE id=?{extra}', (value,))).fetchone():
        raise CRMError('Ответственный, контакт или сделка больше не доступны')


DEAL_SELECT = '''SELECT d.*,u.username AS owner_name,s.name AS supplier_name,b.name AS buyer_name,
    COALESCE((SELECT SUM(amount) FROM crm_expenses WHERE deal_id=d.id AND voided=0),0) AS expenses
    FROM crm_deals d LEFT JOIN users u ON u.id=d.owner_id
    LEFT JOIN crm_contacts s ON s.id=d.supplier_id LEFT JOIN crm_contacts b ON b.id=d.buyer_id'''


def deal_dict(row):
    value = dict(row)
    value['cost'] = (value['purchase'] or 0) + value['expenses']
    value['profit'] = value['sale'] - value['cost'] if value['stage'] == 'sold' else None
    value['expected_profit'] = value['target'] - value['cost'] if value['target'] is not None and value['purchase'] is not None else None
    return value


async def find_source(connection, item_id, source_url, include_archived=False):
    ref = listing_reference(source_url)
    rows = await (await connection.execute('SELECT id,item_id,source_url FROM crm_deals' +
        ('' if include_archived else ' WHERE archived=0') + ' ORDER BY archived,id')).fetchall()
    for row in rows:
        other = listing_reference(row['source_url'])
        if (item_id and row['item_id'] == item_id) or (ref and other and ref[0] == other[0]):
            return row['id']
    return None


class CRMRepository:
    async def crm_find_import(self, user_id, item_id=None, source_url=''):
        async with self.connection() as c:
            c.row_factory = aiosqlite.Row
            await actor(c, user_id)
            return await find_source(c, item_id, source_url, include_archived=True)

    async def crm_telegram_user(self, chat_id):
        async with self.connection() as c:
            c.row_factory = aiosqlite.Row
            row = await (await c.execute('''SELECT u.id,u.username,u.role FROM users u
                JOIN user_telegram_chats t ON t.user_id=u.id WHERE t.chat_id=? AND u.is_active=1''', (chat_id,))).fetchone()
            return dict(row) if row else None

    async def crm_overview(self, user_id):
        async with self.connection() as c:
            c.row_factory = aiosqlite.Row
            await actor(c, user_id)
            rows = await (await c.execute(DEAL_SELECT)).fetchall()
            deals = [deal_dict(row) for row in rows]
            sold = [d for d in deals if d['stage'] == 'sold']
            stock = [d for d in deals if d['stage'] not in ('lead', 'sold', 'cancelled')]
            stages = {key: sum(d['stage'] == key and not d['archived'] for d in deals) for key in STAGES}
            overdue = (await (await c.execute('SELECT COUNT(*) FROM crm_tasks WHERE done=0 AND due_at<=?', (time.time(),))).fetchone())[0]
            users = [dict(row) for row in await (await c.execute('SELECT id,username,role FROM users WHERE is_active=1 ORDER BY username')).fetchall()]
            return {'stages': stages, 'stage_labels': STAGES, 'users': users, 'stock_cost': sum(d['cost'] for d in stock),
                    'revenue': sum(d['sale'] for d in sold), 'profit': sum(d['profit'] for d in sold),
                    'sold_count': len(sold), 'stock_count': len(stock), 'overdue': overdue}

    async def crm_deals(self, user_id, query='', stage='', owner_id=None, archived=False, offset=0, limit=50):
        async with self.connection() as c:
            c.row_factory = aiosqlite.Row
            await actor(c, user_id)
            where, args = ['d.archived=?'], [int(archived)]
            if query:
                where.append('(d.title LIKE ? OR d.serial LIKE ? OR CAST(d.id AS TEXT)=?)')
                args.extend([f'%{query}%', f'%{query}%', query])
            if stage:
                where.append('d.stage=?'); args.append(stage)
            if owner_id:
                where.append('d.owner_id=?'); args.append(owner_id)
            clause = ' WHERE ' + ' AND '.join(where)
            total = (await (await c.execute('SELECT COUNT(*) FROM crm_deals d' + clause, args)).fetchone())[0]
            rows = await (await c.execute(DEAL_SELECT + clause + ' ORDER BY d.updated_at DESC,d.id DESC LIMIT ? OFFSET ?', (*args, limit, offset))).fetchall()
            return {'items': [deal_dict(row) for row in rows], 'total': total}

    async def crm_deal(self, user_id, deal_id):
        async with self.connection() as c:
            c.row_factory = aiosqlite.Row
            await actor(c, user_id)
            row = await (await c.execute(DEAL_SELECT + ' WHERE d.id=?', (deal_id,))).fetchone()
            if not row:
                raise CRMError('Сделка не найдена', 404)
            result = deal_dict(row)
            result['expense_rows'] = [dict(r) for r in await (await c.execute('SELECT * FROM crm_expenses WHERE deal_id=? ORDER BY id DESC', (deal_id,))).fetchall()]
            result['history'] = [dict(r) for r in await (await c.execute('''SELECT h.*,u.username FROM crm_history h JOIN users u ON u.id=h.actor_id
                WHERE entity='deal' AND entity_id=? ORDER BY h.id DESC LIMIT 100''', (deal_id,))).fetchall()]
            return result

    async def crm_save_deal(self, user_id, payload, deal_id=None, version=None, *, reuse_source=False):
        data = DealInput.model_validate(payload).model_dump()
        if data['stage'] not in ('lead', 'cancelled') and data['purchase'] is None:
            raise CRMError('Укажите цену закупки')
        if data['stage'] == 'sold' and data['sale'] is None:
            raise CRMError('Укажите цену продажи')
        if data['stage'] != 'sold' and data['sale'] is not None:
            raise CRMError('Цена продажи указывается для этапа «Продано»')
        async with self.connection() as c:
            c.row_factory = aiosqlite.Row
            await c.execute('BEGIN IMMEDIATE')
            await actor(c, user_id, True)
            if not deal_id:
                existing = await find_source(c, data['item_id'], data['source_url'], include_archived=reuse_source)
                if existing:
                    if reuse_source:
                        await c.rollback()
                        return await self.crm_deal(user_id, existing)
                    raise CRMError(f'Это объявление уже в сделке №{existing}', 409)
            await reference(c, 'users', data['owner_id'])
            for key in ('supplier_id', 'buyer_id'):
                await reference(c, 'crm_contacts', data[key])
            old = None
            if deal_id:
                old = await (await c.execute('SELECT * FROM crm_deals WHERE id=?', (deal_id,))).fetchone()
                if not old:
                    raise CRMError('Сделка не найдена', 404)
                if old['version'] != version:
                    raise CRMError('Сделку изменил коллега. Обновите карточку перед сохранением', 409)
            if data['item_id'] and (not old or data['item_id'] != old['item_id']):
                await reference(c, 'items', data['item_id'])
                duplicate = await (await c.execute('SELECT id FROM crm_deals WHERE item_id=? AND archived=0 AND id<>?', (data['item_id'], deal_id or 0))).fetchone()
                if duplicate:
                    raise CRMError(f'Это объявление уже в сделке №{duplicate[0]}', 409)
            now = time.time()
            data['updated_at'] = now
            data['sold_at'] = (old['sold_at'] if old and old['stage'] == 'sold' else now) if data['stage'] == 'sold' else None
            if old:
                await c.execute('UPDATE crm_deals SET '+','.join(f'{key}=?' for key in data)+',version=version+1 WHERE id=?', (*data.values(), deal_id))
            else:
                data['created_at'] = now
                cursor = await c.execute('INSERT INTO crm_deals('+','.join(data)+') VALUES('+','.join('?' for _ in data)+')', tuple(data.values()))
                deal_id = cursor.lastrowid
            changes = {key: {'before': old[key] if old else None, 'after': value} for key, value in data.items() if key not in ('updated_at', 'created_at', 'sold_at') and (not old or old[key] != value)}
            await history(c, 'deal', deal_id, user_id, 'Изменение' if old else 'Создание', changes)
            await c.commit()
        return await self.crm_deal(user_id, deal_id)

    async def crm_add_expense(self, user_id, deal_id, payload):
        data = ExpenseInput.model_validate(payload).model_dump()
        async with self.connection() as c:
            c.row_factory = aiosqlite.Row
            await c.execute('BEGIN IMMEDIATE')
            await actor(c, user_id, True)
            await reference(c, 'crm_deals', deal_id)
            old = await (await c.execute('SELECT * FROM crm_expenses WHERE request_key=?', (data['request_key'],))).fetchone()
            if old:
                if old['deal_id'] != deal_id or old['actor_id'] != user_id or any(old[k] != data[k] for k in ('amount', 'category', 'note')):
                    raise CRMError('Ключ операции уже использован', 409)
                return dict(old)
            cur = await c.execute('INSERT INTO crm_expenses(deal_id,amount,category,note,actor_id,created_at,request_key) VALUES(?,?,?,?,?,?,?)',
                                  (deal_id, data['amount'], data['category'], data['note'], user_id, time.time(), data['request_key']))
            await history(c, 'deal', deal_id, user_id, 'Расход', {'id': cur.lastrowid, **data})
            await c.execute('UPDATE crm_deals SET updated_at=?,version=version+1 WHERE id=?', (time.time(), deal_id))
            await c.commit()
            return {'id': cur.lastrowid}

    async def crm_void_expense(self, user_id, deal_id, expense_id):
        async with self.connection() as c:
            c.row_factory = aiosqlite.Row
            await c.execute('BEGIN IMMEDIATE')
            await actor(c, user_id, True)
            row = await (await c.execute('SELECT * FROM crm_expenses WHERE id=? AND deal_id=?', (expense_id, deal_id))).fetchone()
            if not row:
                raise CRMError('Расход не найден', 404)
            if not row['voided']:
                await c.execute('UPDATE crm_expenses SET voided=1 WHERE id=?', (expense_id,))
                await history(c, 'deal', deal_id, user_id, 'Отмена расхода', {'id': expense_id, 'amount': row['amount']})
                await c.execute('UPDATE crm_deals SET version=version+1,updated_at=? WHERE id=?', (time.time(), deal_id))
            await c.commit()

    async def crm_records(self, user_id, kind, offset=0, limit=100):
        table = {'contacts': 'crm_contacts', 'tasks': 'crm_tasks'}[kind]
        async with self.connection() as c:
            c.row_factory = aiosqlite.Row
            await actor(c, user_id)
            order = 'done,due_at,id' if kind == 'tasks' else 'name,id'
            rows = await (await c.execute(f'SELECT * FROM {table} ORDER BY {order} LIMIT ? OFFSET ?', (limit, offset))).fetchall()
            total = (await (await c.execute(f'SELECT COUNT(*) FROM {table}')).fetchone())[0]
            return {'items': [dict(row) for row in rows], 'total': total}

    async def crm_save_record(self, user_id, kind, payload, record_id=None, version=None):
        model = {'contacts': ContactInput, 'tasks': TaskInput}[kind]
        data = model.model_validate(payload).model_dump()
        table = {'contacts': 'crm_contacts', 'tasks': 'crm_tasks'}[kind]
        if kind == 'tasks':
            data['due_at'] = data['due_at'].timestamp()
        async with self.connection() as c:
            c.row_factory = aiosqlite.Row
            await c.execute('BEGIN IMMEDIATE')
            await actor(c, user_id, True)
            if kind == 'tasks':
                await reference(c, 'users', data['owner_id'])
                await reference(c, 'crm_deals', data['deal_id'])
            old = None
            if record_id:
                old = await (await c.execute(f'SELECT * FROM {table} WHERE id=?', (record_id,))).fetchone()
                if not old:
                    raise CRMError('Запись не найдена', 404)
                if old['version'] != version:
                    raise CRMError('Запись изменил коллега. Обновите страницу', 409)
                await c.execute(f'UPDATE {table} SET '+','.join(f'{key}=?' for key in data)+',version=version+1 WHERE id=?', (*data.values(), record_id))
            else:
                if kind == 'tasks':
                    data['created_at'] = time.time()
                cur = await c.execute(f'INSERT INTO {table}('+','.join(data)+') VALUES('+','.join('?' for _ in data)+')', tuple(data.values()))
                record_id = cur.lastrowid
            await history(c, kind, record_id, user_id, 'Изменение' if old else 'Создание', {'before': dict(old) if old else None, 'after': data})
            if kind == 'tasks' and data['deal_id']:
                await history(c, 'deal', data['deal_id'], user_id, 'Задача', {'id': record_id, 'title': data['title'], 'done': data['done']})
            row = await (await c.execute(f'SELECT * FROM {table} WHERE id=?', (record_id,))).fetchone()
            await c.commit()
            return dict(row)

    async def crm_queue_reminders(self):
        async with self.connection() as c:
            c.row_factory = aiosqlite.Row
            await c.execute('BEGIN IMMEDIATE')
            rows = await (await c.execute('''SELECT t.*,l.chat_id FROM crm_tasks t JOIN users u ON u.id=t.owner_id
                JOIN user_telegram_chats l ON l.user_id=u.id WHERE t.done=0 AND t.due_at<=? AND u.is_active=1''', (time.time(),))).fetchall()
            for row in rows:
                payload = json.dumps({'type': 'crm_reminder', 'task_id': row['id'], 'due_at': row['due_at'], 'owner_id': row['owner_id']})
                key = f"crm:{row['id']}:{row['due_at']}:{row['owner_id']}:{row['chat_id']}"
                await c.execute('''INSERT OR IGNORE INTO notification_outbox(event_key,channel,destination,payload,available_at,created_at)
                    VALUES(?,'telegram',?,?,?,?)''', (key, str(row['chat_id']), payload, time.time(), time.time()))
            await c.commit()

    async def crm_reminder(self, chat_id, payload):
        async with self.connection() as c:
            c.row_factory = aiosqlite.Row
            row = await (await c.execute('''SELECT t.*,d.title AS deal_title FROM crm_tasks t
                LEFT JOIN crm_deals d ON d.id=t.deal_id JOIN users u ON u.id=t.owner_id
                JOIN user_telegram_chats l ON l.user_id=u.id WHERE t.id=? AND t.owner_id=? AND l.chat_id=?
                AND t.done=0 AND u.is_active=1 AND t.due_at=?''', (payload['task_id'], payload['owner_id'], chat_id, payload['due_at']))).fetchone()
            return dict(row) if row else None
