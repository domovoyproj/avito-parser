import csv
import openpyxl
import unittest
import test_storage
from export_jobs import ExportJobs
from models import AvitoItem


class ExportJobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await test_storage.StorageTests.asyncSetUp(self)

    async def test_csv_and_xlsx_persistent_jobs_filter_escape_and_recovery(self):
        await self.db.save_item(AvitoItem(id='one',title='=unsafe',price=0,url='https://www.avito.ru/one'))
        await self.db.save_item(AvitoItem(id='two',title='hidden',is_hidden=True,price=100,url='https://www.avito.ru/two'))
        await self.db.toggle_item_hidden('two',True)
        worker = ExportJobs(self.db,self.root/'exports')
        for fmt in ('csv','xlsx'):
            job_id = await worker.enqueue(fmt, {})
            async with self.db.connection() as connection:
                await connection.execute("UPDATE export_jobs SET status='running' WHERE id=?", (job_id,))
                await connection.commit()
            self.assertTrue(await worker.process_one())
            state = await worker.get(job_id)
            self.assertEqual((state['status'],state['rows_written']), ('completed',1))
            path = worker.directory/f'export_{job_id}.{fmt}'
            if fmt=='csv':
                with path.open(encoding='utf-8-sig') as stream:
                    rows=list(csv.reader(stream,delimiter=';'))
                self.assertEqual(rows[1][1], "'=unsafe")
                self.assertEqual(rows[1][2], '0')
            else:
                book=openpyxl.load_workbook(path,read_only=True)
                rows=list(book.active.values)
                book.close()
                self.assertEqual(rows[1][1], "'=unsafe")
                self.assertEqual(rows[1][2], 0)
        self.assertFalse(await worker.process_one())

    async def test_unassigned_filter_and_invalid_api_ranges(self):
        from models import SearchQuery
        from routers.exports import ExportRequest
        from pydantic import ValidationError
        search = await self.db.add_search(SearchQuery(name='Assigned',url='https://www.avito.ru/search'))
        for identity, membership in [('assigned',search),('unassigned',None)]:
            await self.db.save_item(AvitoItem(id=identity,title=identity,url='https://www.avito.ru/item',search_query_id=membership))
        worker = ExportJobs(self.db,self.root/'exports')
        job = await worker.enqueue('csv', {'search_query_id':'-1'})
        await worker.process_one()
        self.assertEqual((await worker.get(job))['rows_written'],1)
        with (worker.directory/f'export_{job}.csv').open(encoding='utf-8-sig') as stream:
            self.assertEqual(list(csv.reader(stream,delimiter=';'))[1][0],'unassigned')
        for arguments in ({'search_query_id':'invalid'}, {'min_price':200,'max_price':100}):
            with self.assertRaises(ValidationError):
                ExportRequest(**arguments)
