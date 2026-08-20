import asyncio
from pathlib import Path
import httpx
from config import config
from database import Database, db
from models import AvitoItem, SearchQuery, SellerInfo
from web_server import app


async def run_web_tests():
    print("=== 🧪 Тестирование Веб-Панели Avito Parser Pro ===")

    # 1. Инициализация БД с тестовыми данными
    print("1. Инициализация базы данных и тестовых данных...")
    await db.init_db()

    # Добавляем тестовый поиск
    search_id = await db.add_search(SearchQuery(
        name="MacBook M3 Pro Москва",
        url="https://www.avito.ru/moskva/noutbuki/apple-ASgBAgICAURMyg2YnDg"
    ))
    assert search_id > 0
    print(f"   ✓ Создан поиск #{search_id}")

    # Добавляем тестовый товар
    test_item = AvitoItem(
        id="item_web_test_1",
        title="Apple MacBook Pro 14 M3 Pro 18GB 512GB",
        price=185000,
        old_price=200000,
        price_string="185 000 ₽",
        url="https://www.avito.ru/moskva/noutbuki/macbook_pro_14_test_1",
        address="Москва, Пресненская наб.",
        metro="Деловой центр",
        description="В идеальном состоянии, полный комплект.",
        main_image="https://10.img.avito.st/image/1/test_mac.jpg",
        images=["https://10.img.avito.st/image/1/test_mac.jpg"],
        params={"Процессор": "Apple M3 Pro", "Память": "18 ГБ", "SSD": "512 ГБ"},
        seller=SellerInfo(name="ReStore Resale", rating=4.9, reviews_count=128),
        delivery_available=True,
        search_query_id=search_id
    )
    saved_it, is_new, is_drop, _ = await db.save_item(test_item)
    assert saved_it.id == "item_web_test_1"
    print("   ✓ Сохранен тестовый товар в базу")

    # Имитация падения цены для фиксации в price_history
    test_item_cheaper = test_item.model_copy()
    test_item_cheaper.price = 175000
    _, _, is_drop2, prev = await db.save_item(test_item_cheaper)
    assert is_drop2 is True
    print("   ✓ Зафиксировано падение цены (185k -> 175k ₽)")

    # 2. Тестирование ASGI HTTP запросов через httpx
    print("2. Тестирование авторизации и HTML маршрутов веб-панели...")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Авторизация супер-админа
        login_res = await client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        assert login_res.status_code == 200, f"Login failed: {login_res.text}"
        print("   ✓ Авторизация admin/admin123: OK (200)")

        pages = [
            "/",
            "/parser",
            "/item-parser",
            "/monitoring",
            "/items",
            "/price-drops",
            "/proxies",
            "/settings",
            "/logs"
        ]
        for p in pages:
            resp = await client.get(p)
            assert resp.status_code == 200, f"Страница {p} вернула статус {resp.status_code}"
            assert "<!DOCTYPE html>" in resp.text
            print(f"   ✓ HTML страница '{p}': OK (200)")

        # 3. Тестирование REST API
        print("3. Тестирование REST API эндпоинтов...")

        # /api/dashboard
        dash_res = await client.get("/api/dashboard")
        assert dash_res.status_code == 200
        dash_data = dash_res.json()
        assert "stats" in dash_data
        assert dash_data["stats"]["total_items"] >= 1
        assert "monitoring" in dash_data
        print("   ✓ GET /api/dashboard: OK")

        # /api/items (с пагинацией и фильтрами)
        items_res = await client.get("/api/items?page=1&page_size=10&sort_by=newest")
        assert items_res.status_code == 200
        items_data = items_res.json()
        assert items_data["total"] >= 1
        assert len(items_data["items"]) >= 1
        assert items_data["items"][0]["title"] == "Apple MacBook Pro 14 M3 Pro 18GB 512GB"
        print("   ✓ GET /api/items: OK")

        # /api/items/{id}
        single_res = await client.get("/api/items/item_web_test_1")
        assert single_res.status_code == 200
        single_data = single_res.json()
        assert single_data["item"]["id"] == "item_web_test_1"
        assert len(single_data["price_history"]) >= 1
        print("   ✓ GET /api/items/{id}: OK")

        # /api/searches
        searches_res = await client.get("/api/searches")
        assert searches_res.status_code == 200
        s_data = searches_res.json()
        assert len(s_data) >= 1
        print("   ✓ GET /api/searches: OK")

        # /api/searches (POST - create)
        create_s_res = await client.post("/api/searches", json={
            "name": "PlayStation 5 Slim",
            "url": "https://www.avito.ru/moskva/igry_pristavki_i_programmy/ps5",
            "check_interval_min": 15,
            "enabled": True
        })
        assert create_s_res.status_code == 200
        new_sid = create_s_res.json()["id"]
        print(f"   ✓ POST /api/searches: OK (id={new_sid})")

        # /api/searches/{id}/toggle
        toggle_res = await client.patch(f"/api/searches/{new_sid}/toggle")
        assert toggle_res.status_code == 200
        print("   ✓ PATCH /api/searches/{id}/toggle: OK")

        # /api/price-drops
        drops_res = await client.get("/api/price-drops")
        assert drops_res.status_code == 200
        drops_data = drops_res.json()
        assert len(drops_data) >= 1
        assert drops_data[0]["item_id"] == "item_web_test_1"
        print(f"   ✓ GET /api/price-drops: OK (найдено {len(drops_data)} скидок)")

        # /api/proxies
        proxies_res = await client.get("/api/proxies")
        assert proxies_res.status_code == 200
        print("   ✓ GET /api/proxies: OK")

        # /api/settings
        settings_res = await client.get("/api/settings")
        assert settings_res.status_code == 200
        st_data = settings_res.json()
        assert "scraper" in st_data
        assert "telegram" in st_data
        print("   ✓ GET /api/settings: OK")

        # /api/logs
        logs_res = await client.get("/api/logs")
        assert logs_res.status_code == 200
        print("   ✓ GET /api/logs: OK")

        # /api/export/excel
        export_excel_res = await client.get("/api/export/excel")
        assert export_excel_res.status_code == 200
        assert len(export_excel_res.content) > 1000
        print("   ✓ GET /api/export/excel (генерация и скачивание .xlsx): OK")

        # /api/export/csv
        export_csv_res = await client.get("/api/export/csv")
        assert export_csv_res.status_code == 200
        print("   ✓ GET /api/export/csv (скачивание .csv): OK")

        # /api/export/json
        export_json_res = await client.get("/api/export/json")
        assert export_json_res.status_code == 200
        print("   ✓ GET /api/export/json (скачивание .json): OK")

    print("\n[bold green]🎉 ВСЕ ТЕСТЫ ВЕБ-ПАНЕЛИ УСПЕШНО ПРОЙДЕНЫ![/bold green]")


if __name__ == "__main__":
    asyncio.run(run_web_tests())
