import csv
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from config import config
from models import AvitoItem, ExportFormat


class AvitoExporter:
    def __init__(self, export_dir: Optional[Path] = None):
        self.export_dir = export_dir or config.export_dir
        self.export_dir.mkdir(parents=True, exist_ok=True)

    def _generate_filename(self, prefix: str = "avito_export", ext: str = "xlsx") -> Path:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return self.export_dir / f"{prefix}_{timestamp}.{ext}"

    def export_to_excel(self, items: List[AvitoItem], file_path: Optional[Path] = None) -> Path:
        """
        Профессиональный экспорт в Excel (.xlsx) с подсветкой скидок, 
        кликабельными ссылками на фото и авто-шириной колонок.
        """
        if not file_path:
            file_path = self._generate_filename(prefix="avito_items", ext="xlsx")

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Объявления Авито"
        ws.views.sheetView[0].showGridLines = True

        # Стили
        header_font = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="111827", end_color="111827", fill_type="solid")
        header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

        data_font = Font(name="Segoe UI", size=9)
        price_drop_fill = PatternFill(start_color="E6F4EA", end_color="E6F4EA", fill_type="solid")
        hot_deal_fill = PatternFill(start_color="FFF2E8", end_color="FFF2E8", fill_type="solid")
        link_font = Font(name="Segoe UI", size=9, color="2563EB", underline="single")
        
        thin_side = Side(border_style="thin", color="E5E7EB")
        thin_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

        headers = [
            "ID",
            "Заголовок",
            "Текущая цена (₽)",
            "Старая цена (₽)",
            "Динамика цены",
            "Скидка (%)",
            "AI Score",
            "Градация",
            "Адрес / Город",
            "Метро",
            "Доставка",
            "Продавец",
            "Рейтинг",
            "Отзывы",
            "AI Резюме",
            "Дата публикации",
            "Ссылка на Авито",
            "Фотография"
        ]

        # Запись заголовков
        ws.append(headers)
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_align
            cell.border = thin_border
        ws.row_dimensions[1].height = 26

        # Запись данных
        for row_idx, item in enumerate(items, start=2):
            price_delta_str = ""
            discount_pct_str = ""
            has_discount = False

            if item.old_price and item.price:
                delta = item.price - item.old_price
                if delta != 0:
                    price_delta_str = f"{delta:+,} ₽".replace(",", " ")
                    pct = round(((item.old_price - item.price) / item.old_price) * 100, 1)
                    discount_pct_str = f"{pct}%"
                    if delta < 0:
                        has_discount = True

            row_data = [
                str(item.id),
                item.title,
                item.price or "",
                item.old_price or "",
                price_delta_str,
                discount_pct_str,
                item.deal_score or 50,
                item.deal_grade or "FAIR",
                item.address or "",
                item.metro or "",
                "Да" if item.delivery_available else "Нет",
                item.seller.name if item.seller else "Частное лицо",
                item.seller.rating if item.seller and item.seller.rating else "",
                item.seller.reviews_count if item.seller and item.seller.reviews_count else "",
                item.ai_summary or "",
                item.published_at or "",
                item.url,
                item.main_image or ""
            ]
            ws.append(row_data)

            # Оформление ячеек строки
            for col_idx in range(1, len(headers) + 1):
                cell = ws.cell(row=row_idx, column=col_idx)
                cell.font = data_font
                cell.border = thin_border

                if has_discount:
                    cell.fill = price_drop_fill
                elif item.is_hot_deal:
                    cell.fill = hot_deal_fill

                # Выравнивание чисел
                if col_idx in (3, 4, 12, 13):
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                elif col_idx in (1, 5, 6, 7, 10, 14):
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="center")

                # Форматирование цен в рубли
                if col_idx in (3, 4) and isinstance(cell.value, (int, float)):
                    cell.number_format = "#,##0 ₽"

                # Ссылка на объявление
                if col_idx == 15 and item.url:
                    cell.hyperlink = item.url
                    cell.value = "Открыть на Авито"
                    cell.font = link_font
                    cell.alignment = Alignment(horizontal="center", vertical="center")

                # Ссылка на фото
                if col_idx == 16 and item.main_image:
                    cell.hyperlink = item.main_image
                    cell.value = "Посмотреть фото"
                    cell.font = link_font
                    cell.alignment = Alignment(horizontal="center", vertical="center")

            ws.row_dimensions[row_idx].height = 20

        # Автоматический подбор ширины столбцов
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val_str = str(cell.value or "")
                if len(val_str) > max_len:
                    max_len = len(val_str)
            ws.column_dimensions[col_letter].width = max(min(max_len + 3, 45), 12)

        wb.save(file_path)
        return file_path

    def export_to_csv(self, items: List[AvitoItem], file_path: Optional[Path] = None) -> Path:
        """Экспорт в CSV с кодировкой utf-8-sig для корректного открытия в Excel"""
        if not file_path:
            file_path = self._generate_filename(prefix="avito_items", ext="csv")

        fieldnames = [
            "id", "title", "price", "old_price", "price_string", "deal_score", "deal_grade",
            "ai_summary", "is_hot_deal", "url", "address", "metro", "delivery_available",
            "published_at", "seller_name", "seller_rating", "seller_reviews", "main_image"
        ]

        with open(file_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
            writer.writeheader()
            for it in items:
                writer.writerow({
                    "id": it.id,
                    "title": it.title,
                    "price": it.price or "",
                    "old_price": it.old_price or "",
                    "price_string": it.price_string or "",
                    "deal_score": it.deal_score or 50,
                    "deal_grade": it.deal_grade or "FAIR",
                    "ai_summary": it.ai_summary or "",
                    "is_hot_deal": 1 if it.is_hot_deal else 0,
                    "address": it.address or "",
                    "metro": it.metro or "",
                    "delivery_available": "Да" if it.delivery_available else "Нет",
                    "published_at": it.published_at or "",
                    "seller_name": it.seller.name if it.seller else "",
                    "seller_rating": it.seller.rating if it.seller else "",
                    "seller_reviews": it.seller.reviews_count if it.seller else "",
                    "main_image": it.main_image or ""
                })

        return file_path

    def export_to_json(self, items: List[AvitoItem], file_path: Optional[Path] = None) -> Path:
        """Экспорт всех данных в JSON"""
        if not file_path:
            file_path = self._generate_filename(prefix="avito_items", ext="json")

        data = [item.model_dump(mode="json") for item in items]
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return file_path

    def export_to_html(self, items: List[AvitoItem], file_path: Optional[Path] = None) -> Path:
        """Генерация автономного интерактивного HTML отчета с галереей и поиском"""
        if not file_path:
            file_path = self._generate_filename(prefix="avito_report", ext="html")

        total_count = len(items)
        avg_price = int(sum(it.price for it in items if it.price) / total_count) if total_count > 0 else 0
        drops_count = sum(1 for it in items if it.old_price and it.price and it.price < it.old_price)
        total_val = sum(it.price or 0 for it in items)

        cards_html = []
        for it in items:
            has_discount = it.old_price and it.price and it.price < it.old_price
            discount_pct = round(((it.old_price - it.price) / it.old_price) * 100) if has_discount else 0
            img_src = it.main_image or "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 400 300' fill='%230f172a'%3E%3Crect width='100%25' height='100%25'/%3E%3Ctext x='50%25' y='50%25' fill='%23475569' text-anchor='middle'%3EБез фото%3C/text%3E%3C/svg%3E"
            price_formatted = f"{it.price:,} ₽".replace(",", " ") if it.price else "Цена не указана"
            old_price_formatted = f"{it.old_price:,} ₽".replace(",", " ") if it.old_price else ""

            cards_html.append(f"""
            <div class="card" data-title="{it.title.lower()}" data-price="{it.price or 0}">
                <div class="img-wrap">
                    <img src="{img_src}" loading="lazy" alt="{it.title}">
                    {"<span class='badge badge-drop'>-" + str(discount_pct) + "%</span>" if has_discount else ""}
                    {"<span class='badge badge-hot'>🔥 ВЫГОДНО</span>" if it.is_hot_deal else ""}
                </div>
                <div class="card-body">
                    <div class="title" title="{it.title}">{it.title}</div>
                    <div class="price-row">
                        <span class="price">{price_formatted}</span>
                        {"<span class='old-price'>" + old_price_formatted + "</span>" if old_price_formatted else ""}
                    </div>
                    <div class="meta">{it.address or it.metro or 'Россия'}</div>
                    <a href="{it.url}" target="_blank" class="btn">Открыть на Авито ↗</a>
                </div>
            </div>
            """)

        html_content = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Avito Parser Report — {datetime.now().strftime("%d.%m.%Y %H:%M")}</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #070a11; color: #f1f5f9; padding: 24px; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; padding-bottom: 16px; border-bottom: 1px solid rgba(255,255,255,0.08); margin-bottom: 20px; }}
        .title-h1 {{ font-size: 20px; font-weight: 700; }}
        .kpi-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 20px; }}
        .kpi-card {{ background: #0e1424; border: 1px solid rgba(255,255,255,0.07); padding: 14px; border-radius: 10px; }}
        .kpi-label {{ font-size: 10px; font-weight: 700; text-transform: uppercase; color: #64748b; letter-spacing: 0.05em; }}
        .kpi-val {{ font-size: 22px; font-weight: 700; font-family: monospace; margin-top: 4px; color: #fff; }}
        .toolbar {{ display: flex; gap: 12px; margin-bottom: 20px; }}
        .search-input {{ flex: 1; background: #0c1220; border: 1px solid rgba(255,255,255,0.1); color: #fff; padding: 10px 14px; border-radius: 8px; font-size: 13px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px; }}
        .card {{ background: #0e1424; border: 1px solid rgba(255,255,255,0.07); border-radius: 10px; overflow: hidden; display: flex; flex-direction: column; }}
        .img-wrap {{ position: relative; aspect-ratio: 4/3; background: #000; overflow: hidden; }}
        .img-wrap img {{ width: 100%; height: 100%; object-fit: cover; }}
        .badge {{ position: absolute; padding: 3px 6px; border-radius: 4px; font-size: 10px; font-weight: 700; font-family: monospace; }}
        .badge-drop {{ top: 8px; left: 8px; background: rgba(16,185,129,0.9); color: #fff; }}
        .badge-hot {{ top: 8px; right: 8px; background: rgba(249,115,22,0.9); color: #fff; }}
        .card-body {{ padding: 12px; flex: 1; display: flex; flex-direction: column; justify-content: space-between; gap: 8px; }}
        .title {{ font-size: 12px; font-weight: 600; color: #e2e8f0; line-clamp: 2; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
        .price-row {{ display: flex; align-items: baseline; gap: 6px; }}
        .price {{ font-size: 15px; font-weight: 700; font-family: monospace; color: #38bdf8; }}
        .old-price {{ font-size: 11px; text-decoration: line-through; color: #64748b; font-family: monospace; }}
        .meta {{ font-size: 11px; color: #64748b; font-family: monospace; }}
        .btn {{ display: inline-block; background: #1e293b; color: #93c5fd; text-align: center; text-decoration: none; padding: 6px; border-radius: 6px; font-size: 11px; font-weight: 600; margin-top: 4px; }}
        .btn:hover {{ background: #2563eb; color: #fff; }}
    </style>
</head>
<body>
    <div class="header">
        <div>
            <div class="title-h1">Avito Analytics & Scraper Report</div>
            <div style="font-size: 11px; color: #64748b; font-family: monospace; margin-top: 2px;">Сгенерировано: {datetime.now().strftime("%d.%m.%Y %H:%M:%S")}</div>
        </div>
        <div style="font-size: 12px; font-family: monospace; color: #38bdf8; font-weight: 700;">AVITO PRO ENGINE v2.4.0</div>
    </div>

    <div class="kpi-row">
        <div class="kpi-card">
            <div class="kpi-label">Всего товаров</div>
            <div class="kpi-val">{total_count:,}</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Средняя цена</div>
            <div class="kpi-val">{avg_price:,} ₽</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Падений цен</div>
            <div class="kpi-val" style="color: #34d399;">{drops_count}</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Оценка пула</div>
            <div class="kpi-val">{total_val:,} ₽</div>
        </div>
    </div>

    <div class="toolbar">
        <input type="text" id="offlineSearch" class="search-input" placeholder="Живой поиск по названию в отчете..." oninput="filterOffline()">
    </div>

    <div class="grid" id="itemsGrid">
        {"".join(cards_html)}
    </div>

    <script>
    function filterOffline() {{
        const q = document.getElementById('offlineSearch').value.toLowerCase().trim();
        const cards = document.querySelectorAll('.card');
        cards.forEach(c => {{
            const t = c.getAttribute('data-title') || '';
            c.style.display = (!q || t.includes(q)) ? 'flex' : 'none';
        }});
    }}
    </script>
</body>
</html>"""

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        return file_path

    def export_all_formats(self, items: List[AvitoItem], prefix: str = "avito_export") -> dict:
        """Экспорт одновременно во все поддерживаемые форматы"""
        excel_p = self.export_to_excel(items, self._generate_filename(prefix, "xlsx"))
        csv_p = self.export_to_csv(items, self._generate_filename(prefix, "csv"))
        json_p = self.export_to_json(items, self._generate_filename(prefix, "json"))
        html_p = self.export_to_html(items, self._generate_filename(prefix, "html"))

        return {
            "excel": str(excel_p),
            "csv": str(csv_p),
            "json": str(json_p),
            "html": str(html_p)
        }


exporter = AvitoExporter()
