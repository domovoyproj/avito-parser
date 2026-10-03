import csv
import tempfile
import unittest
from pathlib import Path

import openpyxl
from exporter import AvitoExporter
from models import AvitoItem


class ExportTests(unittest.TestCase):
    def test_safe_exports_keep_columns_urls_and_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            exporter = AvitoExporter(Path(folder))
            item = AvitoItem(id='fixture', title='=HYPERLINK("bad")', price=0,
                             deal_score=0, url='https://www.avito.ru/fixture',
                             ai_summary='original summary', published_at='Сегодня')
            excel = exporter.export_to_excel([item])
            workbook = openpyxl.load_workbook(excel)
            row = workbook.active
            self.assertEqual(row['B2'].value[0], "'")
            self.assertEqual(row['C2'].value, 0)
            self.assertEqual(row['G2'].value, 0)
            self.assertEqual(row['O2'].value, item.ai_summary)
            self.assertEqual(row['P2'].value, item.published_at)
            self.assertEqual(row['Q2'].hyperlink.target, item.url)
            workbook.close()
            with exporter.export_to_csv([item]).open(encoding='utf-8-sig') as stream:
                data = next(csv.DictReader(stream, delimiter=';'))
            self.assertEqual(data['price'], '0')
            self.assertEqual(data['deal_score'], '0')
            self.assertEqual(data['url'], item.url)
            self.assertTrue(data['title'].startswith("'"))
            self.assertNotEqual(exporter._generate_filename(), exporter._generate_filename())

    def test_html_untrusted_text_and_url(self):
        with tempfile.TemporaryDirectory() as folder:
            item = AvitoItem(id='fixture', title='<script>alert(1)</script>',
                             address='<img onerror="alert(1)">', price=0,
                             url='javascript:alert(1)', main_image='javascript:alert(1)')
            text = AvitoExporter(Path(folder)).export_to_html([item]).read_text(encoding='utf-8')
            self.assertNotIn('<script>alert(1)</script>', text)
            self.assertNotIn('javascript:alert', text)
            self.assertIn('&lt;script&gt;', text)
