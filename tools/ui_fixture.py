"""Local UI fixture server: no production DB, credentials, or external sends."""
import asyncio
import json
import time
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from config import config
from database import db
from exporter import exporter
from models import AvitoItem, SearchQuery, SellerInfo, UserRole


async def seed(root):
    db.db_path = root / 'fixture.db'
    config.db_path = db.db_path
    config.export_dir = exporter.export_dir = root / 'exports'
    config.export_dir.mkdir()
    config.telegram.bot_token = ''
    config.telegram.admin_chat_ids = [777]  # Synthetic recipient; token stays empty.
    config.proxy.enabled = False
    await db.init_db()
    await db.bootstrap_admin('admin', 'fixture-password-2026')
    await db.create_user('viewer','fixture-password-2026',UserRole.VIEWER)
    await db.create_user('operator','fixture-password-2026',UserRole.OPERATOR)
    search = await db.add_search(SearchQuery(name='Техника для дома и работы', url='https://www.avito.ru/fixture', enabled=False))
    titles = ['MacBook Air M2 · 16 ГБ / 512 ГБ', 'Sony WH-1000XM5 — наушники', 'Кофемашина DeLonghi Magnifica', 'Фотоаппарат Fujifilm X-S20', 'Монитор Dell UltraSharp 27″', 'Nintendo Switch OLED']
    for i in range(24):
        await db.save_item(AvitoItem(id=f'fixture-{i}', title=titles[i % 6], price=15000 + i*3500,
            old_price=24000+i*3500, url=f'https://www.avito.ru/fixture_{i}', search_query_id=search,
            address='Москва, район Хамовники', description='Полный комплект. Бережное использование. Проверка при встрече.',
            seller=SellerInfo(name='Алексей', rating=4.9, reviews_count=24, is_verified=True), delivery_available=True))

    # Representative UI states, never market evidence or a live Telegram send.
    async with db.connection() as connection:
        await connection.execute("UPDATE items SET deal_score=90,deal_grade='GEM',score_confidence=.83 WHERE id='fixture-0'")
        await connection.execute("UPDATE items SET deal_score=75,deal_grade='HOT',score_confidence=.67 WHERE id IN ('fixture-1','fixture-2','fixture-3')")
        await connection.commit()
    for i,status in ((0,'failed'),(1,'sent')):
        item=await db.get_item_by_id(f'fixture-{i}')
        payload=json.dumps({'item':item.model_dump(mode='json'),'type':'new','search_name':'Тестовый поиск'},ensure_ascii=False)
        async with db.connection() as connection:
            await connection.execute('INSERT INTO notification_outbox(event_key,channel,destination,payload,status,created_at,last_error) VALUES (?,?,?,?,?,?,?)',
                                     (f'ui-{i}','telegram','777',payload,status,time.time(),'HTTP 403' if status=='failed' else None))
            await connection.commit()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as directory:
        asyncio.run(seed(Path(directory)))
        from web_server import app
        uvicorn.run(app, host='127.0.0.1', port=18765, log_level='warning')
