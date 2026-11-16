import json
import unittest
from urllib.parse import quote
from parser_core import AvitoDataExtractor
from parse_outcomes import extract_page


class ParserTests(unittest.TestCase):
    def test_photo_sizes_lazy_loading_and_json_dom_merge(self):
        expected = 'https://30.img.avito.st/photo-large.jpg'
        for raw in ([{'300x200':'//30.img.avito.st/small.jpg','1000x750':expected}],
                    {'sizes':{'1000x750':expected}}, expected, [{'url':{'1000x750':expected}}]):
            item = AvitoDataExtractor._parse_single_json_item({'id':42,'title':'Phone','images':raw})
            self.assertEqual(item.main_image, expected)
        html = '''<div data-marker="item" data-item-id="42"><a data-marker="item-title" href="/phone_42">Phone</a>
        <picture><source srcset="//30.img.avito.st/small.jpg 300w, https://30.img.avito.st/photo-large.jpg 1000w">
        <img src="/static/assets/placeholder.svg"></picture></div>'''
        self.assertEqual(extract_page(html).items[0].main_image, expected)
        json_data = '<script>window.__initialData__ = {"items":[{"id":42,"title":"Phone"}]};</script>'
        self.assertEqual(extract_page(json_data + html).items[0].main_image, expected)
        self.assertIsNone(AvitoDataExtractor.image_url('https:/static/assets/placeholder.svg'))
        self.assertIsNone(AvitoDataExtractor.image_url('javascript:alert(1)'))
        detail = '<h1 data-marker="item-view/title-info">Phone</h1><meta property="og:image" content="' + expected + '">'
        self.assertEqual(AvitoDataExtractor.parse_item_detail_page(detail,'42','https://www.avito.ru/phone_42').main_image,expected)

    def test_json_formats_and_malformed_cards(self):
        for detailed in (None, "not-a-dict", {"value": 0, "string": "Бесплатно"}):
            raw = {"items": [{"id": 42, "title": "Fixture", "price": "1 200 ₽",
                              "priceDetailed": detailed, "delivery": True}, {"title": "invalid"}]}
            for value in (json.dumps(raw), '"' + quote(json.dumps(raw)) + '"'):
                result = extract_page(f"<script>window.__initialData__ = {value};</script>")
                self.assertEqual(result.outcome, "success")
                self.assertEqual(result.total_found, 1)
                self.assertEqual(result.items[0].id, "42")
        self.assertEqual(AvitoDataExtractor._parse_single_json_item({"id": 1, "priceDetailed": {"value": 0}}).price, 0)

    def test_dom_block_and_empty_outcomes(self):
        html = '<div data-marker="item" data-item-id="1"><a data-marker="item-title" href="/fixture_1">Fixture</a><span data-marker="item-price">1200 ₽</span></div>'
        result = extract_page(html)
        self.assertEqual(result.source, "html")
        self.assertEqual(result.items[0].price, 1200)
        self.assertFalse(result.exhaustive)
        self.assertEqual(extract_page('<title>Captcha</title>').outcome, "blocked")
        self.assertEqual(extract_page('<html>Ничего не найдено</html>').outcome, "empty")
        self.assertEqual(extract_page('<html>Неизвестная разметка</html>').outcome,'error')

    def test_item_details_dom_and_json_fixture(self):
        html='''<h1 data-marker="item-view/title-info">Товар</h1>
        <span data-marker="item-view/item-price">Бесплатно</span>
        <div data-marker="item-view/item-description">Полное описание товара</div>
        <span data-marker="delivery/location">Москва</span>
        <div data-marker="image-preview/item"><img src="https://images.test/640x480/photo.jpg"></div>
        <li data-marker="item-params/brand">Бренд: Fixture</li>'''
        item=AvitoDataExtractor.parse_item_detail_page(html,'42','https://www.avito.ru/item_42')
        self.assertEqual(item.title,'Товар')
        self.assertEqual(item.price,0)
        self.assertIn('Полное описание',item.description)
        self.assertEqual(item.params['Бренд'],'Fixture')
        self.assertEqual(item.images,['https://images.test/640x480/photo.jpg'])
        raw={'items':[{'id':42,'title':'JSON details','description':'Данные описания','priceDetailed':{'value':100}}]}
        item=AvitoDataExtractor.parse_item_detail_page('<script>window.__initialData__ = '+json.dumps(raw)+';</script>','42','https://www.avito.ru/item_42')
        self.assertEqual(item.title,'JSON details')
        self.assertEqual(item.price,100)


if __name__ == "__main__":
    unittest.main()
