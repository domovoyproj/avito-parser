import csv
import unittest
import httpx

from catalog_filters import search_mode
from export_jobs import ExportJobs
from models import AvitoItem, UserRole
import test_storage
import test_config
import web_server


class CatalogSearchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await test_storage.StorageTests.asyncSetUp(self)
        for identity, title, description in (
            ('bike', 'Горный велосипед Trek', 'Исправен'),
            ('bike-2', 'Запчасти для велосипеда', 'Колёса и седло'),
            ('sofa', 'Диван синий', 'Адрес <script>alert(1)</script>'),
            ('percent', 'Скидка 10%', 'Спецпредложение'),
        ):
            await self.db.save_item(AvitoItem(id=identity,title=title,description=description,
                                              url=f'https://www.avito.ru/{identity}',price=100))

    async def test_russian_prefix_literal_specials_relevance_and_index_updates(self):
        items, count = await self.db.get_items_filtered(query='велосип', sort_by='relevance')
        self.assertEqual(count, 2)
        self.assertEqual(items[0].id, 'bike')
        self.assertEqual((await self.db.get_items_filtered(query='10%'))[1], 1)
        self.assertEqual((await self.db.get_items_filtered(query='ск'))[1], 1)
        self.assertEqual((await self.db.get_items_filtered(query='г'))[1], 1)
        self.assertEqual((await self.db.get_items_filtered(query='" OR *'))[1], 0)
        self.assertEqual((await self.db.get_items_filtered(query=''))[1], 4)
        await self.db.save_item(AvitoItem(id='bike',title='Новый самокат',url='https://www.avito.ru/bike',price=100))
        self.assertEqual((await self.db.get_items_filtered(query='велосип'))[1], 1)
        self.assertEqual((await self.db.get_items_filtered(query='самокат'))[1], 1)
        async with self.db.connection() as connection:
            await connection.execute("INSERT INTO item_fts(item_fts) VALUES('rebuild')")
            await connection.commit()
        self.assertEqual((await self.db.get_items_filtered(query='самокат'))[1], 1)
        await self.db.delete_item('bike')
        self.assertEqual((await self.db.get_items_filtered(query='самокат'))[1], 0)
        self.assertEqual(search_mode('" OR *')[0], 'literal')
        with self.assertRaises(ValueError):
            search_mode('a' * 201)

    async def test_background_export_matches_catalog_filters(self):
        worker = ExportJobs(self.db,self.root/'exports')
        filters = {'query':'велосип','min_price':50,'hide_reserved':True}
        catalog, total = await self.db.get_items_filtered(**filters)
        job = await worker.enqueue('csv', filters)
        self.assertTrue(await worker.process_one())
        state = await worker.get(job)
        self.assertEqual(state['rows_written'], total)
        with (worker.directory/f'export_{job}.csv').open(encoding='utf-8-sig') as stream:
            rows = list(csv.reader(stream,delimiter=';'))
        self.assertEqual({row[0] for row in rows[1:]}, {item.id for item in catalog})

    async def test_upgrade_rebuilds_existing_items_index(self):
        async with self.db.connection() as connection:
            for trigger in ('items_fts_insert','items_fts_delete','items_fts_update'):
                await connection.execute(f'DROP TRIGGER {trigger}')
            await connection.execute('DROP TABLE item_fts')
            await connection.execute('DELETE FROM schema_migrations WHERE version=10')
            await connection.commit()
        await self.db.init_db()
        self.assertEqual((await self.db.get_items_filtered(query='велосип'))[1], 2)
        await self.db.save_item(AvitoItem(id='new-fts',title='Велосипед детский',url='https://www.avito.ru/new-fts'))
        self.assertEqual((await self.db.get_items_filtered(query='велосип'))[1], 3)

    async def test_saved_filter_isolation(self):
        await self.db.bootstrap_admin('admin','fixture-password-2026')
        _, _, other_id = await self.db.create_user('other','fixture-password-2026',UserRole.OPERATOR)
        admin_id = (await self.db.get_all_users())[0].id
        async with self.db.connection() as connection:
            await connection.execute("INSERT INTO saved_catalog_filters(user_id,name,filters_json,created_at) VALUES (?,?,?,?)",
                                     (admin_id,'Личный','{}','2026-01-01'))
            await connection.commit()
        async with self.db.connection() as connection:
            count = (await (await connection.execute('SELECT COUNT(*) FROM saved_catalog_filters WHERE user_id=?',(other_id,))).fetchone())[0]
        self.assertEqual(count,0)


class SavedFilterAPITests(unittest.IsolatedAsyncioTestCase):
    setUp = test_config.SettingsAPITests.setUp
    asyncSetUp = test_config.SettingsAPITests.asyncSetUp

    async def test_private_filters_and_roles(self):
        response = await self.client.post('/api/saved-filters',json={'name':'Велосипеды','filters':{'query':'велосип'}})
        self.assertEqual(response.status_code, 200, response.text)
        filter_id = response.json()['id']
        self.assertEqual((await self.client.post('/api/saved-filters',json={'name':'Велосипеды','filters':{}})).status_code,409)
        self.assertEqual(len((await self.client.get('/api/saved-filters')).json()),1)
        from database import db
        _, _, user_id = await db.create_user('viewer-search','fixture-password-2026',UserRole.VIEWER)
        self.assertIsNotNone(user_id)
        viewer = httpx.AsyncClient(transport=httpx.ASGITransport(app=web_server.app),base_url='http://test')
        self.addAsyncCleanup(viewer.aclose)
        self.assertEqual((await viewer.post('/api/auth/login',json={'username':'viewer-search','password':'fixture-password-2026'})).status_code,200)
        viewer.headers['X-CSRF-Token']=viewer.cookies['avito_csrf']
        self.assertEqual((await viewer.get('/api/saved-filters')).json(),[])
        self.assertEqual((await viewer.delete(f'/api/saved-filters/{filter_id}')).status_code,403)
        self.assertEqual((await self.client.delete(f'/api/saved-filters/{filter_id}')).status_code,200)

    async def test_catalog_and_direct_csv_export_share_query(self):
        from database import db
        await db.save_item(AvitoItem(id='export-bike',title='Велосипед',url='https://www.avito.ru/export-bike'))
        await db.save_item(AvitoItem(id='export-sofa',title='Диван',url='https://www.avito.ru/export-sofa'))
        catalog = await self.client.get('/api/items',params={'query':'велосип','sort_by':'relevance'})
        self.assertEqual(catalog.status_code,200,catalog.text)
        self.assertEqual([item['id'] for item in catalog.json()['items']],['export-bike'])
        exported = await self.client.get('/api/export/csv',params={'query':'велосип','sort_by':'relevance'})
        self.assertEqual(exported.status_code,200,exported.text)
        text = exported.content.decode('utf-8-sig')
        self.assertIn('export-bike',text)
        self.assertNotIn('export-sofa',text)
