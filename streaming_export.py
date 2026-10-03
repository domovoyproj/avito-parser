"""Bounded-memory CSV/XLSX writer for large, trusted iteration streams."""

import csv
from pathlib import Path
import openpyxl
from openpyxl.cell import WriteOnlyCell
from exporter import spreadsheet_value

HEADERS = [
    "ID",
    "Заголовок",
    "Цена",
    "Старая цена",
    "Динамика цены",
    "Скидка (%)",
    "Оценка",
    "Градация",
    "Адрес",
    "Метро",
    "Доставка",
    "Продавец",
    "Рейтинг",
    "Отзывы",
    "AI резюме",
    "Дата",
    "Ссылка",
    "Фото",
]


def values(item):
    delta = (
        item.price - item.old_price
        if item.price is not None and item.old_price is not None
        else None
    )
    discount = (
        round(100 * (item.old_price - item.price) / item.old_price, 1)
        if item.old_price and item.price is not None
        else None
    )
    return [
        item.id,
        item.title,
        item.price,
        item.old_price,
        delta,
        discount,
        item.deal_score,
        item.deal_grade,
        item.address,
        item.metro,
        item.delivery_available,
        item.seller.name if item.seller else "",
        item.seller.rating if item.seller else None,
        item.seller.reviews_count if item.seller else None,
        item.ai_summary,
        item.published_at,
        item.url,
        item.main_image,
    ]


class StreamingExport:
    def __init__(self, path, fmt):
        self.path = Path(path)
        self.fmt = fmt
        self.stream = None
        if fmt == "csv":
            self.stream = self.path.open("w", encoding="utf-8-sig", newline="")
            self.writer = csv.writer(self.stream, delimiter=";")
            self.writer.writerow(HEADERS)
        elif fmt == "xlsx":
            self.book = openpyxl.Workbook(write_only=True)
            self.sheet = self.book.create_sheet("Объявления")
            self.sheet.append(HEADERS)
        else:
            raise ValueError("Unsupported streaming format")

    def write(self, items):
        for item in items:
            row = [spreadsheet_value(value) for value in values(item)]
            if self.fmt == "csv":
                self.writer.writerow(row)
            else:
                self.sheet.append(
                    [WriteOnlyCell(self.sheet, value=value) for value in row]
                )

    def finish(self):
        if self.stream:
            self.stream.close()
        else:
            self.book.save(self.path)

    def abort(self):
        if self.stream:
            self.stream.close()
        else:
            self.sheet.close()
            self.book.close()
        self.path.unlink(missing_ok=True)
