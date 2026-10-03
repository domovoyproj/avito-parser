"""Offline SQLite latency baseline; synthetic 100k rows, no external services."""
import asyncio
import json
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from database import Database
from models import SearchQuery


async def benchmark():
    with tempfile.TemporaryDirectory() as directory:
        db=Database(Path(directory)/'benchmark.db')
        await db.init_db()
        search=await db.add_search(SearchQuery(name='fixture',url='https://www.avito.ru/fixture'))
        now=datetime.now().isoformat()
        async with db.connection() as connection:
            for offset in range(0,100000,1000):
                rows=[(str(i),'Тестовое объявление',1000+i%10000,'https://www.avito.ru/fixture',search,now,now) for i in range(offset,offset+1000)]
                await connection.executemany('INSERT INTO items(id,title,price,url,search_query_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?)',rows)
                await connection.executemany('INSERT INTO item_searches VALUES (?,?,?)',[(r[0],search,now) for r in rows])
            await connection.commit()
        results={}
        for name,query in [('catalog_page',lambda:db.get_items_filtered(search_query_id=search,limit=24)),
                           ('median',lambda:db.get_search_market_stats(search)),
                           ('dashboard_stats',db.get_stats)]:
            measurements=[]
            for _ in range(20):
                start=time.perf_counter()
                await query()
                measurements.append((time.perf_counter()-start)*1000)
            results[name]={'p50_ms':round(statistics.median(measurements),2),'p95_ms':round(sorted(measurements)[18],2)}
        return {'rows':100000,'samples':20,'queries':results}


if __name__=='__main__':
    print(json.dumps(asyncio.run(benchmark())))
