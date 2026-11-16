import asyncio
import json
import time
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import httpx
from aiogram import types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from config import config
from database import db
from models import UserRole, AvitoItem
from crm_models import DealInput, CRMError
from outbox import OutboxWorker
import test_config
import crm_bot


class CRMTests(unittest.IsolatedAsyncioTestCase):
    setUp = test_config.SettingsAPITests.setUp
    asyncSetUp = test_config.SettingsAPITests.asyncSetUp

    async def make(self, **values):
        result = await self.client.post('/api/crm/deals', json={'title':'Телефон', 'stage':'purchased','purchase':1200000,'target':1800000, **values})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    async def update(self, deal, **values):
        data = {key:deal[key] for key in DealInput.model_fields}
        return await self.client.put('/api/crm/deals/'+str(deal['id']), json={**data,'version':deal['version'],**values})

    async def test_full_cycle_shared_data_profit_and_audit(self):
        _,_,operator = await db.create_user('team','fixture-password-2026',UserRole.OPERATOR)
        contact = (await self.client.post('/api/crm/contacts',json={'name':'Покупатель'})).json()
        d = await self.make(owner_id=operator, buyer_id=contact['id'])
        cost = {'amount':150050,'note':'Ремонт','category':'repair','request_key':'fixture-cost'}
        for _ in range(2):
            r = await self.client.post(f"/api/crm/deals/{d['id']}/expenses",json=cost)
            self.assertEqual(r.status_code,200,r.text)
        d = await db.crm_deal(operator,d['id'])
        self.assertEqual(d['expenses'],150050)
        self.assertIsNone(d['profit'])
        self.assertEqual(d['expected_profit'],449950)
        sold = await self.update(d,stage='sold',sale=1800000)
        self.assertEqual(sold.status_code,200,sold.text)
        self.assertEqual(sold.json()['profit'],449950)
        summary = await db.crm_overview(operator)
        self.assertEqual((summary['stock_cost'],summary['revenue'],summary['profit']),(0,1800000,449950))
        await db.crm_void_expense(operator,d['id'],r.json()['id'])
        self.assertEqual((await db.crm_deal(operator,d['id']))['profit'],600000)
        self.assertTrue(any(h['username']=='team' for h in (await db.crm_deal(operator,d['id']))['history']))
        self.assertIn('Телефон',(await self.client.get('/api/crm/export')).text)
        await db.init_db()
        self.assertEqual((await db.crm_overview(operator))['sold_count'],1)

    async def test_version_conflicts_invalid_money_and_stage(self):
        d = await self.make()
        results = await asyncio.gather(self.update(d,notes='A'),self.update(d,notes='B'))
        self.assertEqual(sorted(r.status_code for r in results),[200,409])
        for values in ({'purchase':-1},{'purchase':1.5},{'purchase':True},{'stage':'sold'}, {'stage':'repair','purchase':None},{'sale':100},{'source_url':'javascript:alert(1)'},{'owner_id':99999}):
            r = await self.client.post('/api/crm/deals',json={'title':'Test',**values})
            self.assertEqual(r.status_code,422,(values,r.text))

    async def test_catalog_import_duplicate_archive_and_loss(self):
        await db.save_item(AvitoItem(id='crm-item',title='Phone',url='https://www.avito.ru/test'))
        d = await self.make(item_id='crm-item')
        duplicate = await self.client.post('/api/crm/deals',json={'title':'duplicate','item_id':'crm-item'})
        self.assertEqual(duplicate.status_code,409)
        sold = (await self.update(d,stage='sold',sale=1000000,archived=True)).json()
        self.assertEqual(sold['profit'],-200000)
        self.assertEqual((await self.client.get('/api/crm/deals')).json()['total'],0)
        self.assertEqual((await self.client.get('/api/crm/deals?archived=true')).json()['total'],1)
        self.assertEqual((await self.client.get('/api/crm/overview')).json()['profit'],-200000)
        self.assertEqual((await self.update(sold,stage='listed',sale=None,archived=False)).status_code,200)
        self.assertEqual((await self.client.get('/api/crm/overview')).json()['sold_count'],0)

    async def test_viewer_and_disabled_user_blocked_web_and_repository(self):
        _,_,viewer = await db.create_user('viewer','fixture-password-2026',UserRole.VIEWER)
        d = await self.make()
        self.assertEqual((await db.crm_deals(viewer))['total'],1)
        with self.assertRaises(CRMError): await db.crm_add_expense(viewer,d['id'],{'amount':100,'note':'x','request_key':'deny'})
        await self.client.post('/api/auth/logout')
        r = await self.client.post('/api/auth/login',json={'username':'viewer','password':'fixture-password-2026'})
        self.assertEqual(r.status_code,200)
        self.client.headers['X-CSRF-Token']=self.client.cookies['avito_csrf']
        self.assertEqual((await self.client.get('/api/crm/deals')).status_code,200)
        self.assertEqual((await self.client.post('/api/crm/contacts',json={'name':'no'})).status_code,403)
        await db.update_user(viewer,is_active=False)
        with self.assertRaises(CRMError): await db.crm_overview(viewer)

    async def test_task_reminder_dedup_edit_complete_and_unlink(self):
        uid=(await db.get_all_users())[0].id
        await db.redeem_telegram_link(await db.issue_telegram_link(uid),123)
        task={'title':'Позвонить','owner_id':uid,'due_at':(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()}
        r=await self.client.post('/api/crm/tasks',json=task)
        self.assertEqual(r.status_code,200,r.text)
        saved=r.json()
        await asyncio.gather(db.crm_queue_reminders(),db.crm_queue_reminders())
        async with db.connection() as c:
            rows=await (await c.execute("SELECT payload FROM notification_outbox WHERE event_key LIKE 'crm:%'")).fetchall()
        self.assertEqual(len(rows),1)
        payload=json.loads(rows[0][0])
        self.assertIsNotNone(await db.crm_reminder(123,payload))
        updated=await self.client.put(f"/api/crm/tasks/{saved['id']}",json={**task,'done':True,'version':saved['version']})
        self.assertEqual(updated.status_code,200,updated.text)
        self.assertIsNone(await db.crm_reminder(123,payload))
        await db.unlink_telegram_chat(uid,123)
        self.assertIsNone(await db.crm_telegram_user(123))
        invalid=await self.client.post('/api/crm/tasks',json={**task,'due_at':'2026-12-01T12:00:00'})
        self.assertEqual(invalid.status_code,422)

    async def test_reminder_delivery_retries_and_rechecks_access(self):
        uid=(await db.get_all_users())[0].id
        await db.redeem_telegram_link(await db.issue_telegram_link(uid),123)
        await db.crm_save_record(uid,'tasks',{'title':'<hello>','owner_id':uid,'due_at':datetime.now(timezone.utc)-timedelta(minutes=1)})
        await db.crm_queue_reminders()
        worker=OutboxWorker(db)
        job=await worker.claim()
        settings=config.telegram.model_copy(deep=True);settings.bot_token='fixture-only'
        response=httpx.Response(200,json={'ok':True})
        with patch.object(config,'telegram',settings),patch('outbox.httpx.AsyncClient.post',AsyncMock(return_value=response)) as send:
            await worker.deliver(job)
            self.assertIn('&lt;hello&gt;',send.call_args.kwargs['json']['text'])
        async with db.connection() as c:
            row=await (await c.execute('SELECT status FROM notification_outbox WHERE id=?',(job['id'],))).fetchone()
        self.assertEqual(row[0],'sent')

    async def test_telegram_identity_group_unlinked_and_revoke(self):
        message=types.Message(message_id=1,date=datetime.now(timezone.utc),chat=types.Chat(id=123,type='private'),from_user=types.User(id=123,is_bot=False,first_name='Test'),text='/crm')
        with self.assertRaises(CRMError):await crm_bot.identity(message)
        uid=(await db.get_all_users())[0].id
        await db.redeem_telegram_link(await db.issue_telegram_link(uid),123)
        self.assertEqual((await crm_bot.identity(message))['id'],uid)
        group=message.model_copy(update={'chat':types.Chat(id=-123,type='group',title='Test')})
        with self.assertRaises(CRMError):await crm_bot.identity(group)
        await db.unlink_telegram_chat(uid,123)
        with self.assertRaises(CRMError):await crm_bot.identity(message)

    async def test_telegram_crud_uses_shared_finances(self):
        uid=(await db.get_all_users())[0].id
        user={'id':uid,'role':'admin'}
        message=SimpleNamespace(answer=AsyncMock())
        await crm_bot.execute(message,user,'add Телефон | 12000 | 18000','tg:1')
        d=(await db.crm_deals(uid))['items'][0]
        await crm_bot.execute(message,user,f"expense {d['id']} | 500,50 | Доставка",'tg:2')
        await crm_bot.execute(message,user,f"sale {d['id']} | 18000",'tg:3')
        self.assertEqual((await db.crm_deal(uid,d['id']))['profit'],549950)
        await crm_bot.execute(message,user,f"set {d['id']} | stage | listed",'tg:4')
        self.assertIsNone((await db.crm_deal(uid,d['id']))['sale'])
        for value in ('NaN','Infinity','1.999','-5'):
            with self.assertRaises(CRMError):crm_bot.money(value)

    async def test_real_dispatcher_routes_linked_team_without_admin_access(self):
        from aiogram import Bot
        from telegram_bot import dp
        _,_,uid=await db.create_user('teammate','fixture-password-2026',UserRole.OPERATOR)
        await db.redeem_telegram_link(await db.issue_telegram_link(uid),456)
        bot=Bot('123456:fixture-token-for-offline-tests')
        self.addAsyncCleanup(bot.session.close)
        sender=types.User(id=456,is_bot=False,first_name='Team')
        chat=types.Chat(id=456,type='private')
        def msg(text,mid=1):return types.Message(message_id=mid,date=datetime.now(timezone.utc),chat=chat,from_user=sender,text=text)
        with patch.object(types.Message,'answer',AsyncMock()) as answer,patch.object(types.CallbackQuery,'answer',AsyncMock()):
            await dp.feed_update(bot,types.Update(update_id=1,message=msg('/crm')))
            self.assertIn('общая база',answer.call_args.args[0])
            answer.reset_mock()
            await dp.feed_update(bot,types.Update(update_id=2,message=msg('/settings')))
            answer.assert_not_awaited()
            event=types.CallbackQuery(id='fixture',from_user=sender,chat_instance='fixture',message=msg('menu'),data='crm:prompt:add')
            await dp.feed_update(bot,types.Update(update_id=3,callback_query=event))
            self.assertIn('название',answer.call_args.args[0])
            await dp.feed_update(bot,types.Update(update_id=4,message=msg('Телефон | 100 | 150',4)))
            self.assertEqual((await db.crm_deals(uid))['total'],1)
            await db.unlink_telegram_chat(uid,456)
            await dp.feed_update(bot,types.Update(update_id=5,message=msg('/crm')))
            self.assertIn('привяжите',answer.call_args.args[0])

    async def test_export_formula_and_html_are_data(self):
        d=await self.make(title='=HYPERLINK("bad") <script>alert(1)</script>')
        self.assertEqual((await self.client.get(f"/api/crm/deals/{d['id']}")).json()['title'],d['title'])
        output=await self.client.get('/api/crm/export')
        self.assertIn("'=HYPERLINK",output.text)


if __name__=='__main__':unittest.main()
