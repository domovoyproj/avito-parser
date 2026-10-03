import json
import unittest
from urllib.parse import quote
from parser_core import AvitoDataExtractor
from parse_outcomes import extract_page


class ParserTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
