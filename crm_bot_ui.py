"""Button-first Telegram CRM: one question at a time, reviewed writes, version checks."""
import html
import secrets
from datetime import datetime, timezone, timedelta
from pydantic import ValidationError
from database import db
from crm_models import DealInput, CRMError, STAGES, ContactInput, TaskInput
from crm_bot import keyboard, money, rub, record

TZ = timezone(timedelta(hours=3))
FIELDS = {'title':'Название', 'purchase':'Цена закупки', 'target':'Плановая цена',
          'notes':'Заметка', 'owner_id':'Ответственный', 'supplier_id':'Поставщик',
          'buyer_id':'Покупатель', 'serial':'Серийный номер', 'listing_url':'Ссылка на продажу',
          'source_url':'Ссылка на закупку'}
CATEGORIES = {'repair':'Ремонт','delivery':'Доставка','commission':'Комиссия','other':'Другое'}


def esc(value):
    return html.escape(str(value or ''))


def writable(user):
    if user['role'] not in ('admin','operator'):
        raise CRMError('У вас доступ только для просмотра',403)


def snapshot(deal):
    return {key:deal[key] for key in DealInput.model_fields}


async def card(message, user, deal_id, detail=False):
    d=await db.crm_deal(user['id'],deal_id)
    lines=[f"<b>{esc(d['title'])}</b>", f"№{d['id']} · {STAGES[d['stage']]}" + (' · В архиве' if d['archived'] else '')]
    if d['purchase'] is not None:lines.append(f"\n{'Цена объявления' if d['stage']=='lead' else 'Закупка'}: <b>{rub(d['purchase'])}</b>")
    if d['expenses']:lines.append(f"Расходы: {rub(d['expenses'])}")
    if d['target'] is not None and d['stage']!='sold':lines.append(f"План продажи: {rub(d['target'])}")
    if d['stage']=='sold':lines.extend([f"Продажа: <b>{rub(d['sale'])}</b>",f"Прибыль: <b>{rub(d['profit'])}</b>"])
    if d['owner_name']:lines.append(f"Ответственный: {esc(d['owner_name'])}")
    if d['notes']:lines.append('\n'+esc(d['notes'][:160])+('…' if len(d['notes'])>160 else ''))
    rows=[]
    if writable_role(user):
        rows=[[('➕ Расход',f'crm:ui:expense:{deal_id}'),('💰 Продать',f'crm:ui:sale:{deal_id}')],
              [('Этап',f'crm:ui:stage:{deal_id}'),('✏️ Изменить',f'crm:ui:edit:{deal_id}')],
              [('Добавить задачу',f'crm:ui:task:{deal_id}')]]
    rows += [[('Подробнее',f'crm:ui:details:{deal_id}'),('Все сделки','crm:list:0')]]
    if detail:
        lines=[f"<b>{esc(d['title'])}</b>",f"№{deal_id} · Подробности"]
        if d['supplier_name']:lines.append('Поставщик: '+esc(d['supplier_name']))
        if d['buyer_name']:lines.append('Покупатель: '+esc(d['buyer_name']))
        if d['serial']:lines.append('Серийный номер: '+esc(d['serial']))
        if d['notes']:lines.append('\n'+esc(d['notes'][:900])+('…' if len(d['notes'])>900 else ''))
        for key,label in (('source_url','Объявление'),('listing_url','Ссылка на продажу')):
            if d[key] and d[key].startswith(('https://','http://')):lines.append(f'<a href="{html.escape(d[key],quote=True)}">{label}</a>')
        lines.append('\n<b>Последние изменения</b>')
        lines.extend(esc(h['username']+': '+h['action']) for h in d['history'][:3])
        rows=[[('Расходы',f'crm:ui:ledger:{deal_id}:0'),('Назад к карточке',f'crm:deal:{deal_id}')]]
    await message.answer('\n'.join(lines),parse_mode='HTML',reply_markup=keyboard(rows),disable_web_page_preview=True)


def writable_role(user):
    return user['role'] in ('admin','operator')


async def select_deal(message,user,action,offset=0):
    result=await db.crm_deals(user['id'],offset=offset,limit=8)
    rows=[[(d['title'][:45],f"crm:ui:{action}:{d['id']}")] for d in result['items']]
    if offset:rows.append([('← Назад',f'crm:ui:choose:{action}:{max(0,offset-8)}')])
    if offset+8<result['total']:rows.append([('Далее →',f'crm:ui:choose:{action}:{offset+8}')])
    if action=='task':rows.append([('Без сделки','crm:ui:task:0')])
    rows.append([('Меню','crm:menu')])
    await message.answer('Выберите сделку:' if result['total'] else 'Сделок пока нет. Добавьте товар или перенесите объявление.',reply_markup=keyboard(rows))


async def ask(message,state,flow,step,text,choices=None,optional=False):
    flow=flow|{'step':step,'token':secrets.token_hex(5)}
    await state.set_state('CRMInput:guided')
    await state.set_data({'ui':flow})
    token=flow['token']
    rows=[[(label,f'crm:ui:pick:{token}:{value}')] for label,value in (choices or [])]
    if optional:rows.append([('Пропустить',f'crm:ui:pick:{token}:skip')])
    if step=='confirm':rows.insert(0,[('✅ Сохранить',f'crm:ui:save:{token}')])
    rows.append([('Отмена',f'crm:ui:cancel:{token}')])
    await message.answer(text,parse_mode='HTML',reply_markup=keyboard(rows))


async def begin(message,user,state,action,deal_id=None,field=None):
    writable(user)
    if action in ('set','expense','sale','stage','edit','task') and deal_id is None:
        await state.clear();await select_deal(message,user,'edit' if action=='set' else action);return
    d=await db.crm_deal(user['id'],deal_id) if deal_id else None
    if action=='edit':
        fields=list(FIELDS.items())
        rows=[[(label,f'crm:ui:field:{deal_id}:{key}') for key,label in fields[i:i+2]] for i in range(0,len(fields),2)]
        rows += [[('Вернуть из архива' if d['archived'] else 'В архив',f'crm:ui:archive:{deal_id}')], [('Назад',f'crm:deal:{deal_id}')]]
        await state.clear()
        await message.answer(f"<b>{esc(d['title'])}</b>\nЧто изменить?",parse_mode='HTML',reply_markup=keyboard(rows));return
    flow={'kind':action,'deal_id':deal_id,'version':d['version'] if d else None,
          'data':snapshot(d) if d else {},'op':secrets.token_hex(12),'actor':user['id']}
    if action=='field':
        if field not in FIELDS:raise CRMError('Откройте карточку заново')
        flow['field']=field
        if field in ('owner_id','supplier_id','buyer_id'):
            await choose_reference(message,user,state,flow,0);return
        prompt={ 'title':'Как назвать товар?', 'purchase':'Введите цену закупки в рублях.', 'target':'За сколько планируете продать? Укажите сумму в рублях.',
                'notes':'Напишите новую заметку. Она заменит прежнюю.', 'serial':'Введите серийный номер или IMEI.',
                'listing_url':'Отправьте ссылку на объявление о продаже.', 'source_url':'Отправьте ссылку на закупку.'}[field]
        current=d[field]
        if current not in (None,''):
            prompt+='\nСейчас: '+esc(rub(current) if field in ('purchase','target') else str(current)[:180])
        clear=field in ('target','notes','serial','listing_url','source_url')
        await ask(message,state,flow,'field',prompt,choices=[('Очистить','clear')] if clear else None);return
    if action=='stage':
        await ask(message,state,flow,'stage',f"<b>{esc(d['title'])}</b>\nВыберите новый этап:",choices=[(v,k) for k,v in STAGES.items()]);return
    if action=='archive':
        flow['data']['archived']=not d['archived']
        await ask(message,state,flow,'confirm',f"{'Вернуть из архива' if d['archived'] else 'Убрать в архив'} «{esc(d['title'])}»?\nИстория и расчёты сохранятся.");return
    if action=='sale':
        flow['data']['stage']='sold'
        if d['purchase'] is None:
            await ask(message,state,flow,'purchase_for_sale','Сначала укажите, за сколько купили товар (₽).');return
        await ask(message,state,flow,'sale','За сколько продали товар? Введите сумму в рублях.');return
    if action=='expense':
        flow['data']={}
        await ask(message,state,flow,'expense_amount',f"<b>{esc(d['title'])}</b>\nСколько потратили? Введите сумму в рублях.");return
    if action=='add':
        flow['data']={'owner_id':user['id'],'stage':'lead'}
        await ask(message,state,flow,'title','Напишите название товара.');return
    if action=='contact':
        flow['data']={}
        await ask(message,state,flow,'contact_name','Как зовут покупателя или поставщика?');return
    if action=='task':
        flow['data']={'owner_id':user['id'],'deal_id':deal_id or None}
        await ask(message,state,flow,'task_title','Что нужно сделать? Напишите задачу.');return
    raise CRMError('Откройте меню /crm заново')


async def choose_reference(message,user,state,flow,offset):
    field=flow['field']
    if field=='owner_id':
        users=(await db.crm_overview(user['id']))['users'];total=len(users)
        rows=users[offset:offset+8];labels=[(u['username'],str(u['id'])) for u in rows]
    else:
        result=await db.crm_records(user['id'],'contacts',offset,8);total=result['total']
        labels=[(c['name'][:50],str(c['id'])) for c in result['items']]
    flow['allowed']=[value for _,value in labels]
    if flow['kind']!='task':
        flow['allowed'].append('clear')
        labels.append(('Не назначен' if field=='owner_id' else 'Не указан','clear'))
    if offset:labels.append(('← Назад',f'page{max(0,offset-8)}'))
    if offset+8<total:labels.append(('Далее →',f'page{offset+8}'))
    flow['pages']=[v for _,v in labels if v.startswith('page')]
    await ask(message,state,flow,'reference',FIELDS[field]+': выберите из списка.',choices=labels)


async def review(message,state,flow,text):
    await ask(message,state,flow,'confirm',text+'\n\nСохранить изменения?')


async def accept(message,user,state,value,from_button=False):
    writable(user)
    flow=(await state.get_data()).get('ui')
    if not flow or flow.get('actor')!=user['id']:raise CRMError('Откройте карточку заново')
    step,data=flow['step'],flow['data']
    if step=='confirm':raise CRMError('Нажмите «Сохранить» или «Отмена» под подтверждением')
    if step=='field':
        field=flow['field']
        if from_button:
            if value!='clear' or field not in ('target','notes','serial','listing_url','source_url'):raise CRMError('Выберите действие заново')
            result=None if field=='target' else ''
        elif field in ('purchase','target'):
            result=money(value)
            if result is None:raise CRMError('Введите сумму цифрами')
        else:result=value
        data[field]=result
        DealInput.model_validate(data)
        shown=rub(result) if field in ('purchase','target') else (str(result)[:350] or 'Убрать значение')
        await review(message,state,flow,FIELDS[field]+': <b>'+esc(shown)+'</b>');return
    if step=='reference':
        if not from_button:raise CRMError('Выберите человека кнопкой из списка')
        if value in flow.get('pages',[]):await choose_reference(message,user,state,flow,int(value[4:]));return
        if value not in flow['allowed']:raise CRMError('Этот вариант больше не доступен')
        field=flow['field'];data[field]=None if value=='clear' else int(value)
        if value=='clear':name='Не назначен' if field=='owner_id' else 'Не указан'
        elif field=='owner_id':
            selected=await db.get_user_by_id(int(value))
            if not selected or not selected.is_active:raise CRMError('Этот сотрудник больше не доступен. Откройте выбор заново.')
            name=selected.username
        else:name=(await record('contacts',int(value),user['id']))['name']
        text=FIELDS[field]+': <b>'+esc(name)+'</b>'
        if flow['kind']=='task':
            text=f"<b>{esc(data['title'])}</b>\nСрок: {datetime.fromisoformat(data['due_at']).strftime('%d.%m.%Y %H:%M')} МСК\n"+text
        await review(message,state,flow,text);return
    if step=='stage':
        if not from_button or value not in STAGES:raise CRMError('Выберите этап кнопкой')
        old=data['stage'];data['stage']=value
        flow['was_sold']=old=='sold' and value!='sold'
        if value=='sold':
            if data['purchase'] is None:await ask(message,state,flow,'purchase_for_sale','За сколько купили товар? Укажите сумму в рублях.');return
            await ask(message,state,flow,'sale','За сколько продали товар? Укажите сумму в рублях.');return
        data['sale']=None
        if value not in ('lead','cancelled') and data['purchase'] is None:
            await ask(message,state,flow,'purchase_for_stage','Для этого этапа нужна цена закупки. Введите сумму в рублях.');return
        await stage_review(message,state,flow);return
    if step in ('purchase_for_sale','purchase_for_stage','sale'):
        amount=money(value)
        if amount is None:raise CRMError('Введите сумму цифрами')
        data['sale' if step=='sale' else 'purchase']=amount
        if step=='purchase_for_sale':await ask(message,state,flow,'sale','За сколько продали товар? Укажите сумму в рублях.');return
        if step=='purchase_for_stage':await stage_review(message,state,flow);return
        d=await db.crm_deal(user['id'],flow['deal_id'])
        profit=data['sale']-data['purchase']-d['expenses']
        await review(message,state,flow,f"Продажа: <b>{rub(amount)}</b>\nПрибыль с учётом текущих расходов: <b>{rub(profit)}</b>");return
    if step=='expense_amount':
        amount=money(value)
        if not amount:raise CRMError('Расход должен быть больше нуля')
        data['amount']=amount
        await ask(message,state,flow,'expense_category','На что потратили?',choices=[(v,k) for k,v in CATEGORIES.items()]);return
    if step=='expense_category':
        if not from_button or value not in CATEGORIES:raise CRMError('Выберите категорию кнопкой')
        data['category']=value
        await ask(message,state,flow,'expense_note','Добавьте комментарий к расходу или нажмите «Пропустить».',optional=True);return
    if step=='expense_note':
        data['note']=CATEGORIES[data['category']] if from_button and value=='skip' else value
        if not 1<=len(data['note'])<=500:raise CRMError('Комментарий: от 1 до 500 символов')
        await review(message,state,flow,f"Расход: <b>{rub(data['amount'])}</b>\n{CATEGORIES[data['category']]} · {esc(data['note'][:300])}");return
    if step=='title':
        if not 1<=len(value)<=200:raise CRMError('Название: от 1 до 200 символов')
        data['title']=value
        await ask(message,state,flow,'new_purchase','За сколько купили? Введите сумму в рублях или нажмите «Ещё не куплено».',choices=[('Ещё не куплено','skip')]);return
    if step=='new_purchase':
        data['purchase']=None if from_button and value=='skip' else money(value)
        data['stage']='purchased' if data['purchase'] is not None else 'lead'
        await ask(message,state,flow,'new_target','За сколько планируете продать? Укажите сумму или пропустите.',optional=True);return
    if step=='new_target':
        data['target']=None if from_button and value=='skip' else money(value)
        await review(message,state,flow,f"<b>{esc(data['title'])}</b>\n{STAGES[data['stage']]}\nЗакупка: {rub(data['purchase'])}"+(f"\nПлан продажи: {rub(data['target'])}" if data['target'] is not None else ''));return
    if step.startswith('contact_'):
        field={'contact_name':'name','contact_phone':'phone','contact_telegram':'telegram','contact_notes':'notes'}[step]
        data[field]='' if from_button and value=='skip' else value
        ContactInput.model_validate(data)
        next_step={'contact_name':('contact_phone','Номер телефона? Можно пропустить.'),'contact_phone':('contact_telegram','Telegram контакта? Можно пропустить.'),'contact_telegram':('contact_notes','Заметка о контакте? Можно пропустить.')}.get(step)
        if next_step:await ask(message,state,flow,*next_step,optional=True);return
        await review(message,state,flow,f"Контакт: <b>{esc(data['name'])}</b>\n{esc(data.get('phone'))} {esc(data.get('telegram'))}");return
    if step=='task_title':
        if not 1<=len(value)<=300:raise CRMError('Задача: от 1 до 300 символов')
        data['title']=value
        await ask(message,state,flow,'task_due','Когда напомнить? Например: <b>20.11.2026 18:00</b>\nВремя московское (UTC+3).');return
    if step=='task_due':
        try:data['due_at']=datetime.strptime(value,'%d.%m.%Y %H:%M').replace(tzinfo=TZ).isoformat()
        except ValueError:raise CRMError('Введите дату и время, например 20.11.2026 18:00')
        flow['field']='owner_id'
        await choose_reference(message,user,state,flow,0);return
    raise CRMError('Откройте карточку заново')


async def stage_review(message,state,flow):
    text='Новый этап: <b>'+STAGES[flow['data']['stage']]+'</b>'
    if flow.get('was_sold'):text+='\nПродажа будет отменена и исключена из выручки и прибыли.'
    await review(message,state,flow,text)


async def save(message,user,state,flow):
    writable(user)
    kind,data=flow['kind'],flow['data']
    did=flow.get('deal_id')
    if kind=='expense':await db.crm_add_expense(user['id'],did,data|{'request_key':'tg-ui:'+flow['op']})
    elif kind=='void':await db.crm_void_expense(user['id'],did,flow['expense_id'])
    elif kind in ('contact','task'):
        result=await db.crm_save_record(user['id'],'contacts' if kind=='contact' else 'tasks',data)
        await state.clear()
        await message.answer('Контакт сохранён.' if kind=='contact' else 'Задача сохранена. Бот напомнит ответственному в срок.',reply_markup=keyboard([[('Меню','crm:menu')]]))
        return
    elif kind=='task_done':
        await db.crm_save_record(user['id'],'tasks',data,flow['record_id'],flow['version'])
        await state.clear();await message.answer('Статус задачи обновлён.',reply_markup=keyboard([[('Задачи','crm:tasks:0')]]));return
    else:
        result=await db.crm_save_deal(user['id'],data,did,flow.get('version'))
        did=result['id']
    await state.set_state(None)
    await state.set_data({'ui_completed':flow['token'],'ui_deal':did})
    await message.answer('✅ Сохранено')
    await card(message,user,did)


async def callback(message,user,state,args):
    try:
        action=args[0]
        if action in ('pick','save','cancel'):
            saved=await state.get_data()
            if action=='save' and saved.get('ui_completed')==args[1]:await card(message,user,saved['ui_deal']);return
            flow=saved.get('ui')
            if not flow or flow['token']!=args[1] or flow['actor']!=user['id']:
                raise CRMError('Эта кнопка устарела. Откройте карточку заново.')
            if action=='cancel':
                await state.clear()
                if flow.get('deal_id'):await card(message,user,flow['deal_id'])
                else:await message.answer('Отменено.',reply_markup=keyboard([[('Меню','crm:menu')]]))
                return
            if action=='save':
                if flow['step']!='confirm':raise CRMError('Сначала заполните данные')
                await save(message,user,state,flow)
            else:await accept(message,user,state,args[2],True)
            return
        await state.clear()
        if action=='details':await card(message,user,int(args[1]),True);return
        if action=='choose':await select_deal(message,user,args[1],int(args[2]));return
        if action=='ledger':
            did,offset=int(args[1]),int(args[2]);d=await db.crm_deal(user['id'],did)
            expenses=[e for e in d['expense_rows'] if not e['voided']]
            rows=[];lines=['<b>Расходы · '+esc(d['title'])+'</b>']
            for e in expenses[offset:offset+6]:
                lines.append(f"{rub(e['amount'])} · {esc(e['note'][:100])}")
                if writable_role(user):rows.append([(f"Отменить: {rub(e['amount'])} · {e['note'][:20]}",f"crm:ui:void:{did}:{e['id']}")])
            if offset:rows.append([('← Назад',f'crm:ui:ledger:{did}:{max(0,offset-6)}')])
            if offset+6<len(expenses):rows.append([('Далее →',f'crm:ui:ledger:{did}:{offset+6}')])
            rows.append([('К карточке',f'crm:deal:{did}')])
            await message.answer('\n'.join(lines) if expenses else 'Расходов пока нет.',parse_mode='HTML',reply_markup=keyboard(rows));return
        if action=='void':
            writable(user);did,eid=int(args[1]),int(args[2]);d=await db.crm_deal(user['id'],did)
            e=next((e for e in d['expense_rows'] if e['id']==eid and not e['voided']),None)
            if not e:raise CRMError('Этот расход уже отменён или не найден')
            await review(message,state,{'kind':'void','deal_id':did,'expense_id':eid,'data':{},'actor':user['id']},f"Отменить расход {rub(e['amount'])} · {esc(e['note'][:200])}?");return
        if action=='taskdone':
            writable(user);t=await record('tasks',int(args[1]),user['id'])
            data={key:t[key] for key in TaskInput.model_fields};data['due_at']=datetime.fromtimestamp(t['due_at'],timezone.utc).isoformat();data['done']=not bool(t['done'])
            await review(message,state,{'kind':'task_done','data':data,'record_id':t['id'],'version':t['version'],'actor':user['id']},f"{'Завершить' if data['done'] else 'Вернуть в работу'} задачу «{esc(t['title'])}»?");return
        await begin(message,user,state,action,int(args[1]) if len(args)>1 else None,args[2] if len(args)>2 else None)
    except (CRMError,ValidationError,ValueError,IndexError) as exc:
        await show_error(message,state,exc)


async def show_error(message,state,exc):
    flow=(await state.get_data()).get('ui',{})
    text=str(exc) if isinstance(exc,CRMError) else 'Проверьте введённое значение.'
    if isinstance(exc,ValidationError):
        issue=exc.errors()[0]
        field=issue['loc'][-1] if issue['loc'] else ''
        label=FIELDS.get(field,{'name':'Имя','phone':'Телефон','telegram':'Telegram'}.get(field,'Значение'))
        if issue['type']=='string_too_long':text=f"{label}: не более {issue['ctx']['max_length']} символов."
        elif issue['type']=='string_too_short':text=f'{label}: поле не должно быть пустым.'
        elif field in ('listing_url','source_url'):text='Отправьте полную ссылку, начинающуюся с https:// или http://, без логина и пароля.'
    rows=[]
    if flow.get('deal_id'):rows.append([('Открыть свежую карточку',f"crm:deal:{flow['deal_id']}")])
    if flow.get('token'):rows.append([('Отмена',f"crm:ui:cancel:{flow['token']}")])
    await message.answer(text,reply_markup=keyboard(rows) if rows else None)


async def input_value(message,user,state):
    text=(message.text or '').strip()
    if text.startswith('/'):
        flow=(await state.get_data()).get('ui',{})
        await state.clear()
        if flow.get('deal_id'):await card(message,user,flow['deal_id'])
        else:await message.answer('Отменено. /crm — меню')
        return
    try:await accept(message,user,state,text)
    except (CRMError,ValidationError,ValueError) as exc:await show_error(message,state,exc)
