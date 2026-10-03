"""Isolated regression checks; no real Avito requests or persistent data."""
import asyncio
import tempfile
from pathlib import Path

import aiosqlite
import httpx

from config import config
from database import db
from exporter import exporter
from models import AvitoItem
from web_server import app
from test_web_panel import run_web_tests


async def check():
    await run_web_tests()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for method, path in [("GET", "/api/dashboard"), ("GET", "/api/export/json"),
                             ("GET", "/api/settings"), ("POST", "/api/monitoring/toggle")]:
            response = await client.request(method, path)
            assert response.status_code == 401, (path, response.status_code)
        response = await client.post("/api/auth/login", json={"username": "admin", "password": "fixture-password-2026"})
        assert response.status_code == 200
        client.headers["X-CSRF-Token"] = client.cookies["avito_csrf"]
        for grade in ("GEM", "HOT", "FAIR", "CAUTION"):
            await db.save_item(AvitoItem(id=f"grade-{grade}", title=grade, url="https://www.avito.ru/test", price=100))
        async with aiosqlite.connect(db.db_path) as connection:
            for grade in ("GEM", "HOT", "FAIR", "CAUTION"):
                await connection.execute("UPDATE items SET deal_grade=? WHERE id=?", (grade, f"grade-{grade}"))
            await connection.commit()
        for grade, expected in [("HOT", {"HOT", "GEM"}), ("FAIR", {"HOT", "GEM", "FAIR"})]:
            response = await client.get("/api/items", params={"deal_grade": grade})
            assert response.status_code == 200
            found = {item["deal_grade"] for item in response.json()["items"]}
            assert found == expected, (grade, found)
        await client.post("/api/auth/logout")
        assert (await client.get("/api/dashboard")).status_code == 401
    print("PASS: anonymous API blocked, session logout enforced, inclusive grade filters")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        db.db_path = root / "test.db"
        config.export_dir = exporter.export_dir = root / "exports"
        config.export_dir.mkdir()
        asyncio.run(check())
