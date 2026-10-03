"""
Тестовый набор для проверки AI Deal Scoring Engine, миграций базы данных и Telegram-интеграции.
"""

import asyncio
import tempfile
import json
from pathlib import Path
from typing import Optional

from ai_scoring import DealScoringEngine, deal_scoring_engine
from database import Database, db
from models import AISettings, AvitoItem, SellerInfo, TelegramChatSettings
from telegram_bot import format_item_notification


def test_deal_scoring_engine_math():
    """Тестирование математических факторов скоринга и выявления рисков"""
    print("\n--- 1. ТЕСТИРОВАНИЕ МАТЕМАТИЧЕСКОГО СКОРИНГА ---")

    # Идеальный GEM-лот: цена на 35% ниже рынка, проверенный продавец 4.9★ (120 отзывов), 5 фото, доставка
    gem_item = AvitoItem(
        id="test_gem_01",
        title="Sony PlayStation 5 Slim 1TB с дисководом (Идеальное состояние)",
        price=35000,
        old_price=42000,
        url="https://www.avito.ru/item_gem_01",
        address="Москва, Тверская ул., 10",
        metro="Тверская",
        description="Продам консоль в идеале, полный заводской комплект, любые проверки у меня дома, чек и гарантия.",
        images=["img1.jpg", "img2.jpg", "img3.jpg", "img4.jpg", "img5.jpg"],
        main_image="img1.jpg",
        params={"Состояние": "Б/у", "Объем памяти": "1 ТБ", "Тип": "С дисководом"},
        seller=SellerInfo(name="Константин", rating=4.9, reviews_count=120, is_verified=True),
        delivery_available=True
    )

    score, grade, reasons, flaws = deal_scoring_engine.evaluate_item(
        gem_item, market_median=54000, market_avg=55000
    )
    print(f"💎 GEM Test: Score = {score}/100, Grade = {grade}")
    print(f"   Факторы ({len(reasons)}): {reasons}")
    print(f"   Риски ({len(flaws)}): {flaws}")

    assert score >= 85, f"Ожидался балл GEM (>=85), получено: {score}"
    assert grade == "GEM", f"Ожидалась градация GEM, получено: {grade}"
    assert len(flaws) == 0, f"У GEM-лота не должно быть рисков: {flaws}"
    assert len(reasons) >= 5, "Должно быть определено не менее 5 позитивных факторов"
    print("✅ GEM-лот успешно прошел валидацию!")

    # Лот с дефектом / на запчасти (CAUTION)
    broken_item = AvitoItem(
        id="test_broken_01",
        title="iPhone 14 Pro 128GB на запчасти не включается разбит экран",
        price=15000,
        url="https://www.avito.ru/item_broken_01",
        description="Упал с высоты, экран разбит вдребезги, не включается и не реагирует на зарядку, заблокирован icloud. Без проверок.",
        seller=SellerInfo(name="Продавец", rating=3.8, reviews_count=2, is_verified=False),
        delivery_available=False
    )

    score_b, grade_b, reasons_b, flaws_b = deal_scoring_engine.evaluate_item(
        broken_item, market_median=65000, market_avg=68000
    )
    print(f"\n⚠️ Flaw/Defect Test: Score = {score_b}/100, Grade = {grade_b}")
    print(f"   Факторы ({len(reasons_b)}): {reasons_b}")
    print(f"   Выявленные риски ({len(flaws_b)}): {flaws_b}")

    assert score_b < 50, f"Ожидался балл CAUTION (<50) для битого товара, получено: {score_b}"
    assert grade_b == "CAUTION", f"Ожидалась градация CAUTION, получено: {grade_b}"
    assert len(flaws_b) >= 3, f"Должно быть выявлено несколько рисков/дефектов, получено: {flaws_b}"
    print("✅ Лот с дефектами и стоп-словами корректно оштрафован!")

    # Подделка / Реплика (CAUTION)
    replica_item = AvitoItem(
        id="test_replica_01",
        title="AirPods Max копия 1:1 люкс качество не оригинал",
        price=7500,
        url="https://www.avito.ru/item_replica_01",
        description="Шикарная реплика 1:1, звук как в оригинале, чип airoha, серийники бьются на сайте.",
        seller=SellerInfo(name="Store", rating=4.6, reviews_count=10, is_verified=False),
        delivery_available=True
    )
    score_r, grade_r, reasons_r, flaws_r = deal_scoring_engine.evaluate_item(
        replica_item, market_median=45000
    )
    print(f"\n⚠️ Replica Test: Score = {score_r}/100, Grade = {grade_r}")
    print(f"   Выявленные риски: {flaws_r}")
    assert any("копия" in f.lower() or "реплика" in f.lower() or "не оригинал" in f.lower() for f in flaws_r), "Маркер реплики должен быть обнаружен!"
    print("✅ Детекция реплик и подделок работает корректно!")


async def test_database_migrations_and_scoring():
    """Тестирование сохранения в БД, расчета скоринга и фильтрации"""
    print("\n--- 2. ТЕСТИРОВАНИЕ БАЗЫ ДАННЫХ И ХРАНЕНИЯ СКОРИНГА ---")
    await db.init_db()

    # Сохраняем тестовый товар
    test_item = AvitoItem(
        id="test_db_item_999",
        title="Ноутбук Apple MacBook Air 13 M2 8/256GB Midnight (Идеал)",
        price=72000,
        old_price=80000,
        url="https://www.avito.ru/test_db_999",
        address="Санкт-Петербург, Невский проспект",
        metro="Маяковская",
        description="Состояние идеальное, 45 циклов АКБ, полный комплект с коробкой, куплен 4 месяца назад.",
        images=["img1.jpg", "img2.jpg", "img3.jpg", "img4.jpg"],
        main_image="img1.jpg",
        params={"Процессор": "Apple M2", "ОЗУ": "8 ГБ", "SSD": "256 ГБ"},
        seller=SellerInfo(name="Алексей", rating=5.0, reviews_count=35, is_verified=True),
        delivery_available=True
    )

    saved_item, is_new, is_drop, prev_p = await db.save_item(test_item)
    print(f"Сохранен товар #{saved_item.id}: Deal Score = {saved_item.deal_score}, Grade = {saved_item.deal_grade}")
    print(f"   Reasons: {saved_item.deal_reasons}")

    assert saved_item.deal_score >= 70, "MacBook с такими характеристиками должен получить score >= 70"
    assert saved_item.deal_grade in ("HOT", "GEM")

    # Читаем из БД через get_item_by_id
    loaded = await db.get_item_by_id("test_db_item_999")
    assert loaded is not None
    assert loaded.deal_score == saved_item.deal_score
    assert loaded.deal_grade == saved_item.deal_grade
    assert len(loaded.deal_reasons) > 0
    print("✅ Чтение и запись в SQLite с новыми полями скоринга проверены!")

    # Проверка фильтрации по min_deal_score
    items_high, total_high = await db.get_items_filtered(min_deal_score=70, limit=10)
    print(f"Найдено товаров со Score 70+: {total_high}")
    for it in items_high:
        assert it.deal_score >= 70, f"Товар #{it.id} имеет score {it.deal_score} < 70"
    print("✅ Фильтрация по min_deal_score работает идеально!")

    # Проверка сортировки по AI Score
    items_sorted, _ = await db.get_items_filtered(sort_by="deal_score_desc", limit=5)
    scores = [it.deal_score for it in items_sorted]
    print(f"Топ-5 товаров по AI Score: {scores}")
    assert scores == sorted(scores, reverse=True), "Сортировка по AI Score должна идти по убыванию"
    print("✅ Сортировка по AI Score работает корректно!")

    # Проверка AISettings
    ai_set = AISettings(
        enabled=True,
        provider="deepseek",
        api_key="sk-testkey123",
        model="deepseek-chat",
        api_base="https://api.deepseek.com"
    )
    saved_ai = await db.save_ai_settings(ai_set)
    loaded_ai = await db.get_ai_settings()
    assert loaded_ai.enabled is True
    assert loaded_ai.provider == "deepseek"
    assert loaded_ai.api_key == "sk-testkey123"
    print("✅ Хранение и чтение AISettings работает корректно!")

    # Удаляем тестовый товар
    await db.delete_item("test_db_item_999")


async def test_telegram_notifications():
    """Тестирование генерации карточек уведомлений с AI Score для Telegram"""
    print("\n--- 3. ТЕСТИРОВАНИЕ ФОРМАТИРОВАНИЯ TELEGRAM УВЕДОМЛЕНИЙ ---")
    item = AvitoItem(
        id="tg_test_01",
        title="Игровая видеокарта RTX 4070 Super 12GB Dual OC",
        price=52000,
        old_price=61000,
        url="https://www.avito.ru/tg_test_01",
        address="Москва, м. Курская",
        metro="Курская",
        description="На пломбах, чек магазина, гарантия еще 2 года. Любые стресс-тесты Furmark/Superposition.",
        images=["gpu.jpg"],
        main_image="gpu.jpg",
        seller=SellerInfo(name="Илья", rating=4.9, reviews_count=84, is_verified=True),
        delivery_available=True,
        deal_score=88,
        deal_grade="GEM",
        deal_reasons=[
            "Цена значительно ниже рынка (-22%)",
            "Проверенный профиль (Паспорт / Госуслуги)",
            "Высокий рейтинг продавца (4.9★)",
            "Доступна безопасная Авито Доставка"
        ],
        ai_summary="Отличная маржинальная сделка для сборки или перепродажи. Пломбы на месте, риск минимален."
    )

    card_text = await format_item_notification(item, "RTX 4070 Super", notif_type="price_drop")
    print("Сгенерированная карточка Telegram:")
    print("--------------------------------------------------")
    print(card_text)
    print("--------------------------------------------------")

    assert "💎 <b>GEM-ЛОТ" in card_text, "В карточке должен присутствовать бейдж GEM-лота"
    assert "Score: 88/100" in card_text, "В карточке должен отображаться AI Score"
    assert "✅" in card_text, "В карточке должны быть позитивные факторы"
    assert "AI-Вердикт" in card_text, "В карточке должно быть резюме ИИ"
    print("✅ Форматирование уведомлений Telegram прошло успешно!")


async def main():
    test_deal_scoring_engine_math()
    await test_database_migrations_and_scoring()
    await test_telegram_notifications()
    print("\n🎉 ВСЕ ТЕСТЫ AI DEAL SCORING УСПЕШНО ПРОЙДЕНЫ!")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        db.db_path = Path(directory) / "scoring.db"
        asyncio.run(main())
