import unittest
from datetime import datetime, timezone
from aiogram import types
from database import db
from models import UserRole
from crm_models import DealInput
from telegram_bot import dp
import test_crm_import


class GuidedCRMTests(unittest.IsolatedAsyncioTestCase):
    setUp=test_crm_import.TelegramImportTests.setUp
    message=test_crm_import.TelegramImportTests.message
    send=test_crm_import.TelegramImportTests.send
    click=test_crm_import.TelegramImportTests.click

    async def asyncSetUp(self):
        await test_crm_import.TelegramImportTests.asyncSetUp(self)
        self.deal=await db.crm_save_deal(self.uid,{'title':'iPhone 15','stage':'purchased','purchase':3300000,'notes':'Проверить экран','owner_id':self.uid})
        self.did=self.deal['id']

    async def choose(self,value):
        token=(await self.state.get_data())['ui']['token']
        await self.click(f'crm:ui:pick:{token}:{value}')

    async def save(self):
        token=(await self.state.get_data())['ui']['token']
        await self.click(f'crm:ui:save:{token}')
        return token

    async def current(self):return await db.crm_deal(self.uid,self.did)

    def buttons(self):
        return [b for row in self.answer.call_args.kwargs['reply_markup'].inline_keyboard for b in row]

    async def test_compact_card_and_scoped_russian_fields(self):
        await self.click(f'crm:deal:{self.did}')
        text=self.answer.call_args.args[0]
        self.assertIn('33 000 ₽',text)
        for unnecessary in ('Продажа:','План:','История:','Прибыль:','Расходы:'):
            self.assertNotIn(unnecessary,text)
        self.assertTrue(any(b.callback_data==f'crm:ui:edit:{self.did}' for b in self.buttons()))
        await self.click(f'crm:ui:edit:{self.did}')
        self.assertTrue(any(b.text=='Цена закупки' for b in self.buttons()))
        self.assertNotIn('purchase',self.answer.call_args.args[0])

    async def test_price_one_value_review_cancel_and_stale_buttons(self):
        await self.click(f'crm:ui:field:{self.did}:purchase')
        await self.send('-5')
        self.assertEqual((await self.current())['purchase'],3300000)
        await self.send('31000,50')
        self.assertEqual((await self.current())['purchase'],3300000)
        self.assertIn('31 000,50 ₽',self.answer.call_args.args[0])
        old=await self.save()
        self.assertEqual((await self.current())['purchase'],3100050)
        await self.click('crm:ui:save:'+old)
        self.assertEqual((await self.current())['version'],2)
        await self.click(f'crm:ui:field:{self.did}:purchase')
        await self.send('100')
        token=(await self.state.get_data())['ui']['token']
        await self.click('crm:ui:cancel:'+token)
        await self.click('crm:ui:save:'+token)
        self.assertEqual((await self.current())['purchase'],3100050)

    async def test_expense_category_preview_repeat_and_void(self):
        await self.click(f'crm:ui:expense:{self.did}')
        await self.send('500')
        await self.choose('delivery')
        await self.choose('skip')
        token=await self.save()
        await self.click('crm:ui:save:'+token)
        d=await self.current()
        self.assertEqual(d['expenses'],50000)
        e=d['expense_rows'][0]
        self.assertEqual(e['note'],'Доставка')
        await self.click(f"crm:ui:void:{self.did}:{e['id']}")
        self.assertEqual((await self.current())['expenses'],50000)
        await self.save()
        self.assertEqual((await self.current())['expenses'],0)

    async def test_sale_and_reversing_sale_require_confirmation(self):
        await self.click(f'crm:ui:sale:{self.did}')
        await self.send('40000')
        self.assertIn('7 000 ₽',self.answer.call_args.args[0])
        self.assertEqual((await self.current())['stage'],'purchased')
        await self.save()
        self.assertEqual((await self.current())['profit'],700000)
        await self.click(f'crm:ui:stage:{self.did}')
        await self.choose('listed')
        self.assertIn('Продажа будет отменена',self.answer.call_args.args[0])
        self.assertEqual((await self.current())['stage'],'sold')
        await self.save()
        self.assertIsNone((await self.current())['sale'])

    async def test_teammate_edit_conflict_does_not_overwrite(self):
        await self.click(f'crm:ui:field:{self.did}:purchase')
        await self.send('30000')
        d=await self.current()
        await db.crm_save_deal(self.uid,{k:d[k] for k in DealInput.model_fields}|{'notes':'Правка коллеги'},self.did,d['version'])
        await self.save()
        self.assertIn('коллега',self.answer.call_args.args[0])
        d=await self.current()
        self.assertEqual((d['purchase'],d['notes']),(3300000,'Правка коллеги'))

    async def test_names_not_ids_for_owner_and_contacts(self):
        _,_,owner=await db.create_user('employee','fixture-password-2026',UserRole.OPERATOR)
        contact=await db.crm_save_record(self.uid,'contacts',{'name':'Алексей'})
        for field,value,label in (('owner_id',owner,'employee'),('buyer_id',contact['id'],'Алексей')):
            await self.click(f'crm:ui:field:{self.did}:{field}')
            self.assertTrue(any(b.text==label for b in self.buttons()))
            await self.choose(str(value))
            await self.save()
            self.assertEqual((await self.current())[field],value)
        _,_,removed=await db.create_user('removed','fixture-password-2026',UserRole.OPERATOR)
        await self.click(f'crm:ui:field:{self.did}:owner_id')
        await db.delete_user(removed)
        await self.choose(str(removed))
        self.assertIn('больше не доступен',self.answer.call_args.args[0])
        self.assertEqual((await self.current())['owner_id'],owner)

    async def test_new_deal_contact_and_task_wizards(self):
        await self.click('crm:prompt:add')
        await self.send('MacBook')
        await self.choose('skip')
        await self.choose('skip')
        await self.save()
        self.assertEqual((await db.crm_deals(self.uid))['total'],2)
        await self.click('crm:prompt:contact')
        await self.send('Иван')
        for _ in range(3):await self.choose('skip')
        await self.save()
        self.assertEqual((await db.crm_records(self.uid,'contacts'))['items'][0]['name'],'Иван')
        await self.click(f'crm:ui:task:{self.did}')
        await self.send('Проверить аккумулятор')
        await self.send('20.11.2099 18:00')
        await self.choose(str(self.uid))
        await self.save()
        task=(await db.crm_records(self.uid,'tasks'))['items'][0]
        self.assertEqual((task['owner_id'],task['deal_id']),(self.uid,self.did))
        await self.click(f"crm:ui:taskdone:{task['id']}")
        await self.save()
        self.assertTrue((await db.crm_records(self.uid,'tasks'))['items'][0]['done'])

    async def test_old_edit_button_from_existing_card_opens_field_menu(self):
        message=self.message(f'#{self.did} · iPhone 15\nЗакупка: 33 000.00 ₽')
        event=types.CallbackQuery(id='old',from_user=message.from_user,chat_instance='fixture',message=message,data='crm:prompt:set')
        await dp.feed_update(self.bot,types.Update(update_id=999,callback_query=event))
        self.assertTrue(any(b.callback_data==f'crm:ui:field:{self.did}:purchase' for b in self.buttons()))
        self.assertNotIn('|',self.answer.call_args.args[0])

    async def back(self):
        flow=(await self.state.get_data())['ui']
        await self.click('crm:ui:back:'+flow['token'])

    async def test_back_restores_draft_and_invalidates_old_confirmation(self):
        await self.click('crm:prompt:add')
        await self.send('Первое название')
        await self.send('12000')
        await self.choose('skip')
        old=(await self.state.get_data())['ui']['token']
        await self.back()  # target
        await self.back()  # purchase, before the old answer
        self.assertNotIn('purchase',(await self.state.get_data())['ui']['data'])
        await self.back()  # title
        await self.send('Исправленное название')
        await self.choose('skip')
        await self.send('15000')
        await self.click('crm:ui:save:'+old)
        self.assertEqual((await db.crm_deals(self.uid))['total'],1)
        await self.save()
        created=(await db.crm_deals(self.uid))['items'][0]
        self.assertEqual((created['title'],created['purchase'],created['target']),('Исправленное название',None,1500000))

    async def test_back_from_field_preview_and_expense_category(self):
        await self.click(f'crm:ui:field:{self.did}:purchase')
        await self.send('1')
        await self.back()
        self.assertIn('33 000',self.answer.call_args.args[0])
        await self.send('32000')
        await self.save()
        await self.click(f'crm:ui:expense:{self.did}')
        await self.send('500')
        await self.choose('repair')
        await self.back()
        await self.choose('delivery')
        await self.choose('skip')
        await self.save()
        self.assertEqual((await self.current())['expense_rows'][0]['category'],'delivery')

    async def test_contact_buttons_and_task_quick_date_edit(self):
        c=await db.crm_save_record(self.uid,'contacts',{'name':'Поставщик'})
        await self.click('crm:contacts:0')
        self.assertTrue(any(b.callback_data==f"crm:ui:contact:{c['id']}" for b in self.buttons()))
        await self.click(f"crm:ui:contactedit:{c['id']}:phone")
        await self.send('+7 999 123-45-67')
        await self.save()
        self.assertIn('+7 999',self.answer.call_args.args[0])
        await self.click(f'crm:ui:task:{self.did}')
        await self.send('Позвонить')
        await self.send('01.01.2000 12:00')
        self.assertIn('уже прошёл',self.answer.call_args.args[0])
        await self.choose('tomorrow')
        await self.choose(str(self.uid))
        await self.save()
        t=(await db.crm_records(self.uid,'tasks'))['items'][0]
        self.assertGreater(t['due_at'],datetime.now(timezone.utc).timestamp())
        await self.click(f"crm:ui:taskedit:{t['id']}:title")
        await self.send('Встретиться')
        await self.save()
        self.assertIn('Встретиться',self.answer.call_args.args[0])
        await self.click(f"crm:ui:taskedit:{t['id']}:due_at")
        await self.choose('hour')
        await self.save()
        self.assertLess((await db.crm_records(self.uid,'tasks'))['items'][0]['due_at'],t['due_at'])

    async def test_full_note_pagination_and_archive_navigation(self):
        d=await self.current();note='Начало '+('длинная заметка '*250)+' КОНЕЦ'
        await db.crm_save_deal(self.uid,{k:d[k] for k in DealInput.model_fields}|{'notes':note},self.did,d['version'])
        await self.click(f'crm:ui:details:{self.did}')
        self.assertTrue(any(b.text=='Заметка целиком' for b in self.buttons()))
        parts=[]
        for offset in range(0,len(note),1800):
            await self.click(f'crm:ui:notes:{self.did}:{offset}')
            parts.append(self.answer.call_args.args[0])
        self.assertEqual(''.join(parts),note)
        await self.click('crm:list:0')
        self.assertTrue(any(b.callback_data=='crm:archived:0' for b in self.buttons()))

    async def test_revoke_link_before_save_and_viewer_no_mutation_buttons(self):
        await self.click(f'crm:ui:field:{self.did}:notes')
        await self.send('Не сохранять')
        token=(await self.state.get_data())['ui']['token']
        await db.unlink_telegram_chat(self.uid,self.chat_id)
        await self.click('crm:ui:save:'+token)
        self.assertEqual((await self.current())['notes'],'Проверить экран')
        _,_,viewer=await db.create_user('viewerui','fixture-password-2026',UserRole.VIEWER)
        await db.redeem_telegram_link(await db.issue_telegram_link(viewer),self.chat_id)
        await self.click(f'crm:deal:{self.did}')
        self.assertFalse(any('edit' in (b.callback_data or '') for b in self.buttons()))
        await self.click(f'crm:ui:field:{self.did}:notes')
        self.assertIn('только для просмотра',self.answer.call_args.args[0])


if __name__=='__main__':unittest.main()
