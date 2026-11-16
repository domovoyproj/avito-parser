import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from aiogram import Bot, types
from database import db
from models import AvitoItem, UserRole
from crm_models import DealInput, CRMError
from crm_links import listing_reference, message_links, import_button
from telegram_bot import dp, build_item_keyboard
import test_config


class TelegramImportTests(unittest.IsolatedAsyncioTestCase):
    setUp = test_config.SettingsAPITests.setUp

    async def asyncSetUp(self):
        await test_config.SettingsAPITests.asyncSetUp(self)
        self.uid=(await db.get_all_users())[0].id
        self.chat_id=987654
        await db.redeem_telegram_link(await db.issue_telegram_link(self.uid),self.chat_id)
        self.bot=Bot('123456:fixture-token-for-offline-tests')
        self.addAsyncCleanup(self.bot.session.close)
        self.answer=AsyncMock()
        for cls in (types.Message,types.CallbackQuery):
            p=patch.object(cls,'answer',self.answer if cls is types.Message else AsyncMock())
            p.start();self.addCleanup(p.stop)
        self.state=dp.fsm.get_context(bot=self.bot,chat_id=self.chat_id,user_id=self.chat_id)
        await self.state.clear()
        self.addAsyncCleanup(self.state.clear)
        self.seq=100
        self.url='https://www.avito.ru/moskva/telefony/iphone_8517716531'
        await db.save_item(AvitoItem(id='8517716531',title='iPhone <13>',price=12000,url=self.url,description='Описание телефона'))

    def message(self,text=None,**kwargs):
        self.seq+=1
        return types.Message(message_id=self.seq,date=datetime.now(timezone.utc),chat=types.Chat(id=self.chat_id,type='private'),
            from_user=types.User(id=self.chat_id,is_bot=False,first_name='Fixture'),text=text,**kwargs)

    async def send(self,text=None,**kwargs):
        msg=self.message(text,**kwargs)
        await dp.feed_update(self.bot,types.Update(update_id=self.seq,message=msg))

    async def click(self,data):
        msg=self.message('Notification',entities=[types.MessageEntity(type='text_link',offset=0,length=12,url=self.url)])
        event=types.CallbackQuery(id=str(self.seq),from_user=msg.from_user,chat_instance='fixture',message=msg,data=data)
        await dp.feed_update(self.bot,types.Update(update_id=self.seq,callback_query=event))

    async def confirm(self):
        data=await self.state.get_data()
        await self.click('crm:saveimport:'+data['import_nonce'])

    async def test_notification_button_preview_confirm_repeated_and_owner(self):
        await self.click('crm:import:8517716531')
        self.assertEqual((await db.crm_deals(self.uid))['total'],0)
        self.assertIn('iPhone &lt;13&gt;',self.answer.call_args.args[0])
        data=await self.state.get_data()
        await self.confirm()
        await self.click('crm:saveimport:'+data['import_nonce'])
        await self.click('crm:import:8517716531')
        result=await db.crm_deals(self.uid)
        self.assertEqual(result['total'],1)
        d=result['items'][0]
        self.assertEqual((d['purchase'],d['owner_id'],d['stage'],d['notes']),(1200000,self.uid,'lead','Описание телефона'))
        self.assertEqual((await db.crm_overview(self.uid))['stock_cost'],0)

    async def test_share_link_and_forwarded_photo_caption(self):
        await self.send('Нашёл телефон '+self.url+'?utm_source=telegram')
        self.assertEqual((await self.state.get_data())['import_draft']['item_id'],'8517716531')
        # Hidden link on an emoji-bearing caption must not depend on byte offsets.
        await self.send(caption='📦 Открыть объявление',caption_entities=[types.MessageEntity(type='text_link',offset=3,length=18,url=self.url)],
                        photo=[types.PhotoSize(file_id='fixture',file_unique_id='fixture',width=10,height=10)])
        await self.confirm()
        self.assertEqual((await db.crm_deals(self.uid))['total'],1)

    async def test_purchase_requires_actual_price_and_rejects_invalid_amount(self):
        await self.click('crm:import:8517716531')
        nonce=(await self.state.get_data())['import_nonce']
        await self.click('crm:buyimport:'+nonce)
        await self.send('-')
        self.assertIn('фактическая',self.answer.call_args.args[0])
        await self.send('-100')
        self.assertEqual((await db.crm_deals(self.uid))['total'],0)
        await self.send('11500,50')
        await self.confirm()
        d=(await db.crm_deals(self.uid))['items'][0]
        self.assertEqual((d['stage'],d['purchase']),('purchased',1150050))

    async def test_old_preview_cannot_confirm_changed_price(self):
        await self.click('crm:import:8517716531')
        old=(await self.state.get_data())['import_nonce']
        await self.click('crm:buyimport:'+old)
        await self.click('crm:saveimport:'+old)
        self.assertEqual((await db.crm_deals(self.uid))['total'],0)
        await self.send('10000')
        await self.click('crm:saveimport:'+old)
        self.assertEqual((await db.crm_deals(self.uid))['total'],0)
        await self.confirm()
        self.assertEqual((await db.crm_deals(self.uid))['items'][0]['purchase'],1000000)

    async def test_unknown_link_manual_fields_cancel_and_stale_preview(self):
        url='https://m.avito.ru/moskva/telefony/phone_9000000001?share=1'
        await self.send(url)
        self.assertEqual(await self.state.get_state(),'CRMInput:import_name')
        await self.send('Телефон без каталога')
        await self.send('-')
        stale=(await self.state.get_data())['import_nonce']
        await self.send('/cancel')
        await self.click('crm:saveimport:'+stale)
        self.assertEqual((await db.crm_deals(self.uid))['total'],0)
        await self.send('/crm import '+url)
        await self.send('Новый телефон')
        await self.send('8000')
        await self.confirm()
        d=(await db.crm_deals(self.uid))['items'][0]
        self.assertIsNone(d['item_id'])
        self.assertEqual(d['source_url'],'https://www.avito.ru/moskva/telefony/phone_9000000001')

    async def test_concurrent_team_import_reuses_web_record_and_archive(self):
        _,_,other=await db.create_user('importer','fixture-password-2026',UserRole.OPERATOR)
        data={'title':'Phone','source_url':self.url+'?tracking=1','purchase':1200000,'item_id':'8517716531','owner_id':self.uid}
        a,b=await asyncio.gather(db.crm_save_deal(self.uid,data,reuse_source=True),db.crm_save_deal(other,data|{'owner_id':other},reuse_source=True))
        self.assertEqual(a['id'],b['id'])
        await db.crm_save_deal(self.uid,{k:a[k] for k in DealInput.model_fields}|{'archived':True},a['id'],a['version'])
        await self.send(self.url)
        self.assertIn('В архиве',self.answer.call_args.args[0])
        self.assertEqual((await db.crm_deals(self.uid,archived=True))['total'],1)

    async def test_viewer_unlinked_and_revoked_before_confirm_cannot_create(self):
        await self.click('crm:import:8517716531')
        nonce=(await self.state.get_data())['import_nonce']
        await db.unlink_telegram_chat(self.uid,self.chat_id)
        await self.click('crm:saveimport:'+nonce)
        self.assertEqual((await db.crm_deals(self.uid))['total'],0)
        await self.send(self.url)
        self.assertIn('привяжите',self.answer.call_args.args[0])
        _,_,viewer=await db.create_user('viewimport','fixture-password-2026',UserRole.VIEWER)
        await db.redeem_telegram_link(await db.issue_telegram_link(viewer),self.chat_id)
        await self.send(self.url)
        self.assertIn('только для просмотра',self.answer.call_args.args[0])
        with self.assertRaises(CRMError):await db.crm_save_deal(viewer,{'title':'No','source_url':self.url},reuse_source=True)

    async def test_link_validation_multiple_and_button_lengths(self):
        for url in ('https://avito.ru.evil.test/item_8517716531','https://avito.ru@evil.test/item_8517716531','javascript:alert(1)','https://www.avito.ru/moskva/telefony',self.url+' '+self.url):
            self.assertIsNone(listing_reference(url))
        await self.send(self.url+' https://www.avito.ru/test_9000000001')
        self.assertIn('несколько',self.answer.call_args.args[0])
        self.assertEqual((await db.crm_deals(self.uid))['total'],0)
        self.assertLessEqual(len(import_button('я'*100)['callback_data'].encode()),64)
        buttons=build_item_keyboard('8517716531',self.url).inline_keyboard
        self.assertTrue(any(b.callback_data=='crm:import:8517716531' for row in buttons for b in row))


if __name__=='__main__':unittest.main()
