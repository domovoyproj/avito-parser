import unittest
from unittest.mock import patch

import httpx

from database import db
from models import AvitoItem, UserRole
from tools.evaluate_feedback import evaluate
import test_storage
import test_config
import web_server


class FeedbackStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await test_storage.StorageTests.asyncSetUp(self)
        await self.db.bootstrap_admin('admin', 'fixture-password-2026')
        self.user_id = (await self.db.get_all_users())[0].id
        await self.db.save_item(AvitoItem(id='feedback', title='Example', price=100,
                                          url='https://www.avito.ru/feedback', category='test'))

    async def test_snapshot_idempotence_and_offline_evaluation(self):
        first = await self.db.record_item_feedback('feedback', self.user_id, 'helpful', 'Private note')
        self.assertTrue(first['created'])
        self.assertFalse((await self.db.record_item_feedback('feedback', self.user_id, 'helpful', 'Private note'))['created'])
        async with self.db.connection() as connection:
            await connection.execute("UPDATE items SET deal_score=99,score_version='future-3' WHERE id='feedback'")
            await connection.commit()
        history = await self.db.item_feedback_history('feedback', self.user_id)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['score_version'], 'deterministic-2')
        self.assertNotEqual(history[0]['score'], 99)
        await self.db.record_item_feedback('feedback', self.user_id, 'false_positive')
        report = await self.db.feedback_report(category='test', score_version='future-3')
        self.assertEqual(report['count'], 1)
        self.assertEqual(report['labels'], {'false_positive': 1})
        self.assertFalse(report['calibration_ready'])
        report = evaluate(self.db.db_path)
        self.assertEqual(len(report['examples']), 1)
        self.assertNotIn('Private note', str(report))
        self.assertNotIn('feedback', str(report['examples']))

    async def test_missing_item_and_empty_report(self):
        self.assertIsNone(await self.db.record_item_feedback('missing', self.user_id, 'helpful'))
        report = await self.db.feedback_report()
        self.assertEqual(report['count'], 0)
        self.assertIsNone(report['false_positive_rate'])


class FeedbackAPITests(unittest.IsolatedAsyncioTestCase):
    setUp = test_config.SettingsAPITests.setUp
    asyncSetUp = test_config.SettingsAPITests.asyncSetUp

    async def test_authorization_and_private_history(self):
        await db.save_item(AvitoItem(id='feedback-api', title='Example', url='https://www.avito.ru/example'))
        result = await self.client.post('/api/items/feedback-api/feedback', json={'label': 'helpful'})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['created'])
        self.assertFalse((await self.client.post('/api/items/feedback-api/feedback', json={'label': 'helpful'})).json()['created'])
        self.assertEqual((await self.client.post('/api/items/feedback-api/feedback', json={'label': 'invalid'})).status_code, 422)
        self.assertEqual((await self.client.get('/api/items/feedback-api/feedback')).json()['history'][0]['author'], 'admin')
        _, _, viewer_id = await db.create_user('viewer-feedback', 'fixture-password-2026', UserRole.VIEWER)
        self.assertIsNotNone(viewer_id)
        viewer = httpx.AsyncClient(transport=httpx.ASGITransport(app=web_server.app), base_url='http://test')
        self.addAsyncCleanup(viewer.aclose)
        self.assertEqual((await viewer.post('/api/auth/login', json={'username':'viewer-feedback','password':'fixture-password-2026'})).status_code, 200)
        viewer.headers['X-CSRF-Token'] = viewer.cookies['avito_csrf']
        self.assertEqual((await viewer.post('/api/items/feedback-api/feedback', json={'label':'stale'})).status_code, 403)
        self.assertEqual((await viewer.get('/api/items/feedback-api/feedback')).json()['history'], [])
        self.assertEqual((await viewer.get('/api/feedback/report')).json()['count'], 1)
