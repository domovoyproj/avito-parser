import json
import re
import urllib.parse
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup

from models import AvitoItem, SellerInfo


class AvitoDataExtractor:
    @staticmethod
    def image_url(value):
        if not isinstance(value, str):
            return None
        value = value.strip()
        if value.startswith('//'):
            value = 'https:' + value
        try:
            parsed = urllib.parse.urlsplit(value)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                return None
            if parsed.path.lower().endswith(('.svg', 'blank.gif')) or '/static/' in parsed.path or '/assets/' in parsed.path:
                return None
        except ValueError:
            return None
        return value

    @classmethod
    def image_urls(cls, value):
        """Accept galleries, nested size maps and dimension keys of any resolution."""
        if isinstance(value, str):
            url = cls.image_url(value)
            return [url] if url else []
        if isinstance(value, list):
            return list(dict.fromkeys(url for entry in value for url in cls.image_urls(entry)))
        if isinstance(value, dict):
            sizes = [(int(m[1]) * int(m[2]), child) for key, child in value.items()
                     if (m := re.fullmatch(r'(\d+)x(\d+)', str(key)))]
            for _, candidate in sorted(sizes, key=lambda entry: entry[0], reverse=True):
                urls = cls.image_urls(candidate)
                if urls:
                    return urls[:1]
            for key in ('sizes', 'urls', 'url', 'src', 'imageLarge', 'image', 'images', 'photos'):
                urls = cls.image_urls(value.get(key))
                if urls:
                    return urls
        return []

    @classmethod
    def dom_images(cls, root):
        images = []
        for element in root.select('img, picture source'):
            candidates = []
            for key in ('data-srcset', 'srcset'):
                variants = []
                for part in (element.get(key) or '').split(','):
                    fields = part.strip().split()
                    if fields:
                        try:
                            size = float(fields[1].rstrip('wx')) if len(fields) > 1 else 0
                        except ValueError:
                            size = 0
                        variants.append((size, fields[0]))
                candidates.extend(url for _, url in sorted(variants, key=lambda entry: entry[0], reverse=True))
            candidates.extend(element.get(key) for key in ('data-src', 'data-url', 'src'))
            chosen = next((url for candidate in candidates if (url := cls.image_url(candidate))), None)
            if chosen and chosen not in images:
                images.append(chosen)
        return images

    @staticmethod
    def clean_price(price_str: Optional[str]) -> Optional[int]:
        """Очистка строки цены и перевод в int (например '15 500  ₽/мес.' -> 15500)"""
        if not price_str:
            return None
        if str(price_str).strip().lower() in ('бесплатно', 'даром', 'free'):
            return 0
        # Удаляем неразрывные пробелы и спецсимволы
        cleaned = str(price_str).replace('&nbsp;', ' ')
        match = re.search(r'(?:\d{1,3}(?:[ \xa0\u202f]\d{3})+|\d+)(?:[.,]\d{1,2})?', cleaned)
        if match:
            try:
                # Do not concatenate a monthly payment, old price or quantity
                # into the advertised price. Amounts are stored in whole rubles.
                amount = re.sub(r'[ \xa0\u202f]', '', match.group()).replace(',', '.')
                return int(amount.split('.')[0])
            except ValueError:
                return None
        return None

    @classmethod
    def extract_from_initial_data(cls, html: str) -> List[AvitoItem]:
        """
        Извлечение объявлений из встроенного JSON (window.__initialData__).
        Это самый полный и точный источник данных Авито.
        """
        items: List[AvitoItem] = []
        
        # 1. Поиск URI-encoded JSON
        match = re.search(r'window\.__initialData__\s*=\s*"(.*?)";', html)
        data = None

        if match:
            try:
                decoded = urllib.parse.unquote(match.group(1))
                data = json.loads(decoded)
            except Exception:
                data = None

        # 2. Поиск прямого JSON объекта в скрипте
        if not data:
            match_raw = re.search(r'window\.__initialData__\s*=\s*(\{.*?\});\s*</script>', html, re.DOTALL)
            if match_raw:
                try:
                    data = json.loads(match_raw.group(1))
                except Exception:
                    data = None

        if not data or not isinstance(data, dict):
            return items

        # Рекурсивный поиск блока items в структуре __initialData__
        catalog_items = cls._find_catalog_items_in_json(data)
        for raw_item in catalog_items:
            try:
                item = cls._parse_single_json_item(raw_item)
                if item:
                    items.append(item)
            except Exception:
                continue

        return items

    @classmethod
    def _find_catalog_items_in_json(cls, data: Any) -> List[Dict[str, Any]]:
        """Поиск списка товаров в дереве JSON любой вложенности"""
        found: List[Dict[str, Any]] = []

        if isinstance(data, dict):
            # Проверяем стандартные ключи каталога Авито
            if "items" in data and isinstance(data["items"], list):
                # Проверим, что это элементы каталога (имеют id или title)
                if any("id" in it or "title" in it for it in data["items"] if isinstance(it, dict)):
                    return data["items"]

            for v in data.values():
                res = cls._find_catalog_items_in_json(v)
                if res:
                    return res
        elif isinstance(data, list):
            for it in data:
                res = cls._find_catalog_items_in_json(it)
                if res:
                    return res

        return found

    @classmethod
    def _parse_single_json_item(cls, raw: Dict[str, Any]) -> Optional[AvitoItem]:
        """Преобразование единичного элемента JSON Авито в модель AvitoItem"""
        item_id = str(raw.get("id") or raw.get("itemId") or "")
        if not item_id:
            return None

        title = raw.get("title") or raw.get("name") or "Без названия"
        
        # Получение цены
        price = None
        price_detailed = raw.get("priceDetailed")
        if isinstance(price_detailed, dict):
            price = price_detailed.get("value")
        elif "price" in raw:
            if isinstance(raw["price"], (int, float)):
                price = int(raw["price"])
            elif isinstance(raw["price"], str):
                price = cls.clean_price(raw["price"])

        price_string = (price_detailed.get("string") if isinstance(price_detailed, dict) else None) or (f"{price} ₽" if price is not None else "Цена не указана")

        # URL
        url_path = raw.get("urlPath") or raw.get("uri") or ""
        if url_path.startswith("http"):
            url = url_path
        else:
            url = f"https://www.avito.ru{url_path}" if url_path else f"https://www.avito.ru/item/{item_id}"

        # Адрес и локация
        location = raw.get("location") or {}
        address = location.get("name") if isinstance(location, dict) else str(location)
        metro = None
        if isinstance(location, dict):
            metro = location.get("metro", {}).get("name") if isinstance(location.get("metro"), dict) else None

        # Фотографии
        images = []
        for field in ('images', 'previewImages', 'gallery', 'photos', 'image', 'mainImage', 'photo', 'preview'):
            images = cls.image_urls(raw.get(field))
            if images:
                break

        main_image = images[0] if images else None
        # Продавец
        seller_info = None
        seller_raw = raw.get("seller") or raw.get("user") or {}
        if isinstance(seller_raw, dict):
            is_verif = bool(seller_raw.get("isVerified") or seller_raw.get("verified") or seller_raw.get("hasBadge"))
            raw_score = seller_raw.get("score") or seller_raw.get("rating")
            rating = None
            if raw_score is not None:
                try:
                    rating = float(str(raw_score).replace(",", "."))
                except (ValueError, TypeError):
                    rating = None

            raw_rc = seller_raw.get("reviewCount") or seller_raw.get("reviewsCount")
            reviews_count = None
            if raw_rc is not None:
                try:
                    digits = re.findall(r"\d+", str(raw_rc))
                    reviews_count = int("".join(digits)) if digits else None
                except (ValueError, TypeError):
                    reviews_count = None

            seller_info = SellerInfo(
                name=seller_raw.get("name") or seller_raw.get("title"),
                rating=rating,
                reviews_count=reviews_count,
                seller_type=seller_raw.get("type"),
                profile_url=f"https://www.avito.ru{seller_raw.get('urlPath')}" if seller_raw.get("urlPath") else None,
                is_verified=is_verif
            )
        # Характеристики
        params: Dict[str, str] = {}
        if "params" in raw and isinstance(raw["params"], list):
            for p in raw["params"]:
                if isinstance(p, dict) and "title" in p and "value" in p:
                    params[p["title"]] = str(p["value"])

        # Флаги
        delivery_raw = raw.get("delivery")
        delivery = bool(raw.get("hasDelivery") or (delivery_raw.get("isAvailable") if isinstance(delivery_raw, dict) else False))
        is_vip = bool(raw.get("isVip") or raw.get("isPromoted"))

        # Проверка статуса брони / резерва / закрытия
        status = str(raw.get("status") or "").lower()
        is_closed = status in ("closed", "archived", "sold", "removed", "inactive", "blocked")
        
        is_reserved = bool(
            raw.get("isReserved") or
            raw.get("reserved") or
            (raw.get("delivery", {}).get("isReserved") if isinstance(raw.get("delivery"), dict) else False)
        )

        badges_raw = raw.get("badges") or raw.get("badge") or []
        if isinstance(badges_raw, dict):
            badges_raw = [badges_raw]
        if isinstance(badges_raw, list):
            for b in badges_raw:
                if isinstance(b, dict):
                    b_text = str(b.get("title") or b.get("text") or b.get("name") or "").lower()
                    if "зарезервирован" in b_text or "бронь" in b_text:
                        is_reserved = True
                    if "продан" in b_text or "закрыт" in b_text or "снят" in b_text:
                        is_closed = True

        desc = (
            raw.get("description") or
            raw.get("body") or
            raw.get("shortDescription") or
            raw.get("snippet") or
            raw.get("ivaSnippet") or
            raw.get("itemDescription") or
            raw.get("text")
        )
        if not desc and isinstance(raw.get("extra"), dict):
            desc = raw["extra"].get("description") or raw["extra"].get("snippet")
        if isinstance(desc, str):
            desc = desc.strip()

        return AvitoItem(
            id=item_id,
            title=title,
            price=price,
            price_string=price_string,
            url=url,
            address=address,
            metro=metro,
            published_at=raw.get("time") or raw.get("date"),
            description=desc or None,
            images=images,
            main_image=main_image,
            params=params,
            seller=seller_info,
            delivery_available=delivery,
            is_vip=is_vip,
            is_reserved=is_reserved,
            is_closed=is_closed,
            category=raw.get("category", {}).get("name") if isinstance(raw.get("category"), dict) else None
        )
    @classmethod
    def extract_from_dom(cls, html: str) -> List[AvitoItem]:
        """
        Фоллбэк-парсер HTML DOM через BeautifulSoup и data-marker атрибуты.
        Работает, даже если структура JSON изменена.
        """
        items: List[AvitoItem] = []
        soup = BeautifulSoup(html, "lxml")

        # Основные контейнеры объявлений на Авито
        item_cards = soup.select("[data-marker='item']")
        if not item_cards:
            item_cards = soup.select("div[data-item-id], div[class*='iva-item-root']")

        for card in item_cards:
            try:
                item_id = card.get("data-item-id") or card.get("id") or ""
                if not item_id:
                    # Попробуем извлечь ID из ссылки
                    link_el = card.select_one("a[data-marker='item-title'], a[href*='/_'], a[itemprop='url']")
                    if link_el and link_el.get("href"):
                        id_match = re.search(r'_(\d+)(?:\?|$)', link_el["href"])
                        if id_match:
                            item_id = id_match.group(1)

                if not item_id:
                    continue

                # Заголовок
                title_el = card.select_one("[itemprop='name'], [data-marker='item-title'], h2, h3, a[title]")
                title = title_el.get_text(strip=True) if title_el else "Без названия"

                # Ссылка
                link_el = card.select_one("a[data-marker='item-title'], a[itemprop='url'], a[href*='/']")
                url_path = link_el.get("href", "") if link_el else ""
                url = url_path if url_path.startswith("http") else f"https://www.avito.ru{url_path}"

                # Цена
                price_el = card.select_one("[data-marker='item-price-value'], [data-marker='item-price'], [itemprop='price'], [class*='price-root'], strong")
                price_str = price_el.get_text(strip=True) if price_el else None
                price = cls.clean_price(price_str)

                # Адрес и метро
                geo_el = card.select_one("[data-marker='item-address'], [class*='geo-root'], [class*='location-root'], [class*='geo-address'], [class*='style-item-address']")
                address = geo_el.get_text(strip=True) if geo_el else None

                metro_el = card.select_one("[class*='geo-marker'], [class*='metro'], [data-marker='item-metro']")
                metro = metro_el.get_text(strip=True) if metro_el else None

                # Дата публикации
                date_el = card.select_one("[data-marker='item-date'], [class*='date-text'], [class*='date_root']")
                published_at = date_el.get_text(strip=True) if date_el else None

                # Главное изображение и галерея
                images = cls.dom_images(card)

                main_image = images[0] if images else None

                # Продавец и рейтинг
                seller_name_el = card.select_one("[data-marker*='seller-name'], [class*='seller-info-name']")
                seller_name = seller_name_el.get_text(strip=True) if seller_name_el else None

                score_el = card.select_one("[data-marker*='seller'][data-marker*='score'], [data-marker*='rating']")
                seller_rating = None
                if score_el:
                    try:
                        seller_rating = float(score_el.get_text(strip=True).replace(",", "."))
                    except ValueError:
                        pass

                reviews_el = card.select_one("[data-marker*='seller-info/summary'], [data-marker*='seller'][data-marker*='review']")
                reviews_count = None
                if reviews_el:
                    m = re.search(r'\d+', reviews_el.get_text(strip=True))
                    if m:
                        reviews_count = int(m.group(0))

                # Проверка бейджа верификации
                is_verified = (
                    card.select_one("[data-marker*='badge'], [class*='badge-title']") is not None or
                    "проверены" in card.get_text(strip=True).lower()
                )

                seller = SellerInfo(
                    name=seller_name,
                    rating=seller_rating,
                    reviews_count=reviews_count,
                    is_verified=is_verified
                ) if (seller_name or seller_rating) else None
                # Авито доставка
                # Авито доставка и статус резерва
                card_text_lower = card.get_text(" ", strip=True).lower()
                delivery = (
                    card.select_one("[data-marker='delivery-badge']") is not None or
                    "доставка" in card_text_lower
                )
                is_reserved = (
                    "зарезервирован" in card_text_lower or
                    "забронирован" in card_text_lower or
                    card.select_one("[data-marker*='reserved'], [class*='reserved'], [class*='badge-item-reserved']") is not None
                )
                is_closed = (
                    "снято с публикации" in card_text_lower or
                    "объявление закрыто" in card_text_lower or
                    "товар продан" in card_text_lower or
                    "продано" in card_text_lower
                )
                desc_el = card.select_one(
                    "div[class*='bottomBlock'] p, [data-marker='item-description'], "
                    "div[class*='iva-item-description'], p[class*='noAccent'], "
                    "div[class*='description'] p, div[class*='snippet'] p, "
                    "div[class*='ivaItemRedesign'] p[class*='ellipsis']"
                )
                description = desc_el.get_text(strip=True) if desc_el else None
                items.append(AvitoItem(
                    id=str(item_id),
                    title=title,
                    price=price,
                    price_string=price_str,
                    url=url,
                    address=address,
                    metro=metro,
                    published_at=published_at,
                    description=description,
                    main_image=main_image,
                    images=images,
                    seller=seller,
                    delivery_available=delivery,
                    is_reserved=is_reserved,
                    is_closed=is_closed
                ))
            except Exception:
                continue

        return items

    @classmethod
    def parse_item_detail_page(cls, html: str, item_id: str, url: str) -> AvitoItem:
        """
        Детальный парсинг страницы конкретного объявления:
        - Полное описание
        - Все фото в максимальном разрешении
        - Характеристики товара/автомобиля/недвижимости
        - Продавец, рейтинг, отзывы
        """
        # Сначала пробуем вытащить данные из JSON
        json_items = cls.extract_from_initial_data(html)
        json_item = None
        for it in json_items:
            if it.id == item_id or item_id in it.url:
                json_item = it
                if it.description and it.images:
                    return it

        # Если в JSON не всё или он не распарсился — парсим DOM
        soup = BeautifulSoup(html, "lxml")

        # Заголовок
        title_el = soup.select_one("h1[data-marker='item-view/title-info'], h1")
        title = title_el.get_text(strip=True) if title_el else (json_item.title if json_item else "Без названия")

        # Цена
        price_el = soup.select_one("[data-marker='item-view/item-price'], span[itemprop='price']")
        price_str = price_el.get_text(strip=True) if price_el else None
        price = cls.clean_price(price_str)

        # Описание
        desc_el = soup.select_one(
            "[data-marker='item-view/item-description'], [data-marker='item-description/text'], "
            "[data-marker='item-description/html'], div[itemprop='description'], "
            "div[class*='style-item-description-text'], div[class*='item-description-text'], "
            "div[class*='style-item-description'], div[class*='styles-module-item-description'], "
            "div[class*='item-description'], div[class*='description-root'], "
            "div[class*='item-view-description'], div[class*='item-view-main'] p, "
            "p[data-marker='item-description/text']"
        )
        description = desc_el.get_text(separator="\n", strip=True) if desc_el else ""
        # Адрес
        address_el = soup.select_one("[data-marker='delivery/location'], [itemprop='address'], span[class*='style-item-address']")
        address = address_el.get_text(strip=True) if address_el else None

        # Все фотографии
        images = []
        for gallery in soup.select("[data-marker^='image-preview'], [data-marker^='image-frame'], [data-marker='item-view/gallery']"):
            images.extend(cls.dom_images(gallery))
        for meta in soup.select('meta[property="og:image"], meta[name="twitter:image"]'):
            images.extend(cls.image_urls(meta.get('content')))
        images = list(dict.fromkeys(images)) or (json_item.images if json_item else [])

        # Характеристики
        params: Dict[str, str] = {}
        for param_li in soup.select("li[data-marker*='item-params'], li[class*='params-item'], li[class*='params-paramsList-item'], div[data-marker='item-view/item-params'] li, [class*='item-params-list'] li, div[class*='params-root'] li"):
            text = param_li.get_text(strip=True)
            if ":" in text:
                k, v = text.split(":", 1)
                params[k.strip()] = v.strip()
            elif "—" in text:
                k, v = text.split("—", 1)
                params[k.strip()] = v.strip()
        # Продавец
        seller_name_el = soup.select_one("[data-marker='seller-info/name'], a[data-marker='seller-link']")
        seller_name = seller_name_el.get_text(strip=True) if seller_name_el else None

        seller_rating_el = soup.select_one("[data-marker='seller-rating/score']")
        seller_rating = None
        if seller_rating_el:
            try:
                seller_rating = float(seller_rating_el.get_text(strip=True).replace(",", "."))
            except ValueError:
                pass

        seller_reviews_el = soup.select_one("[data-marker='seller-rating/reviews-count']")
        reviews_count = None
        if seller_reviews_el:
            m = re.search(r'\d+', seller_reviews_el.get_text(strip=True))
            if m:
                reviews_count = int(m.group(0))

        seller = SellerInfo(
            name=seller_name,
            rating=seller_rating,
            reviews_count=reviews_count
        ) if seller_name else None

        return AvitoItem(
            id=item_id,
            title=title,
            price=price if price is not None else (json_item.price if json_item else None),
            price_string=price_str,
            url=url,
            address=address,
            description=description or (json_item.description if json_item else None),
            images=images,
            main_image=images[0] if images else None,
            params=params,
            seller=seller
        )
