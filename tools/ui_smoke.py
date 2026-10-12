"""Browser QA against tools/ui_fixture.py, saving before/after screenshots."""
import argparse
import asyncio
import json
import time
from pathlib import Path
from playwright.async_api import async_playwright


async def run(destination):
    destination.mkdir(parents=True, exist_ok=True)
    errors = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto('http://127.0.0.1:18765/login')
        await page.screenshot(path=str(destination / 'login-1440.png'), full_page=True)
        await page.click('#login-show-password')
        assert await page.get_attribute('#input-password','type') == 'text'
        await page.click('#login-show-password')
        await page.route('**/api/auth/login',lambda route: route.fulfill(status=429,headers={'Retry-After':'1'},content_type='application/json',body='{"detail":"Повторите вход чуть позже"}'))
        await page.fill('#input-username','admin')
        await page.fill('#input-password','fixture-password-2026')
        await page.click('#btn-login-submit')
        await page.wait_for_function("document.getElementById('btn-login-submit').disabled")
        await page.wait_for_function("!document.getElementById('btn-login-submit').disabled")
        await page.unroute('**/api/auth/login')
        await page.fill('#input-username', 'admin')
        await page.fill('#input-password', 'fixture-password-2026')
        await page.click('#btn-login-submit')
        await page.wait_for_url('http://127.0.0.1:18765/')
        for width in (1440, 768, 360):
            await page.set_viewport_size({'width': width, 'height': 1000})
            for path, name in (('/', 'dashboard'), ('/items', 'catalog'), ('/watchlists', 'watchlists'), ('/feedback', 'feedback'), ('/monitoring', 'monitoring'),
                               ('/settings', 'settings'), ('/parser', 'parser'), ('/logs', 'logs'), ('/exports', 'exports'),
                               ('/item-parser','item-parser'), ('/price-drops','price-drops'), ('/proxies','proxies'), ('/admin/users','users')):
                await page.goto('http://127.0.0.1:18765' + path)
                await page.wait_for_timeout(500)
                if path == '/':
                    await page.wait_for_function("document.getElementById('metric-items').textContent.trim() === '24'")
                await page.screenshot(path=str(destination / f'{name}-{width}.png'), full_page=True)
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (path, width, 'overflow')
                assert await page.evaluate("document.getElementById('main-content').scrollWidth <= document.getElementById('main-content').clientWidth"), (path,width,'main overflow')
                if path == '/items':
                    assert await page.evaluate("document.querySelector('.catalog-toolbar').scrollWidth <= document.querySelector('.catalog-toolbar').clientWidth"), (path,width,'filters clipped')
            if width == 360:
                await page.click('#mobile-menu')
                await page.wait_for_function("document.body.classList.contains('nav-open')")
                assert await page.get_attribute('#mobile-menu','aria-expanded') == 'true'
                await page.keyboard.press('Escape')
                assert await page.locator('#mobile-menu').evaluate('(el)=>document.activeElement===el')
        await page.set_viewport_size({'width': 1440, 'height': 1000})
        await page.goto('http://127.0.0.1:18765/')
        await page.click('.theme-switch')
        await page.reload()
        await page.wait_for_function("document.getElementById('metric-items').textContent.trim() === '24'")
        assert await page.get_attribute('html', 'data-theme') == 'dark'
        await page.screenshot(path=str(destination / 'dashboard-dark-1440.png'), full_page=True)
        await page.click('.theme-switch')
        await page.goto('http://127.0.0.1:18765/items')
        await page.wait_for_selector('.product-card')
        await page.click('#btn-view-table')
        assert await page.get_attribute('#btn-view-table','aria-pressed') == 'true'
        await page.screenshot(path=str(destination / 'catalog-table-1440.png'), full_page=True)
        await page.click('#btn-view-grid')
        opener = page.locator('button[onclick^="openItemModal"]').first
        await opener.focus()
        await opener.click()
        await page.wait_for_selector('#item-modal:not(.hidden)')
        await page.fill('#calc-buy','30000')
        await page.fill('#calc-sale','40000')
        await page.fill('#calc-costs','1500')
        await page.wait_for_function("document.getElementById('calc-profit').textContent.replace(/\\s/g,'').includes('8500')")
        await page.screenshot(path=str(destination / 'item-detail-1440.png'), full_page=True)
        await page.fill('#calc-sale','20000')
        assert await page.locator('#calc-result-row').evaluate("(el)=>el.classList.contains('negative')")
        await page.fill('#calc-sale','0')
        assert 'не определена' in await page.locator('#calc-margin').text_content()
        await page.wait_for_function("document.getElementById('item-feedback-status').textContent.includes('пока нет')")
        await page.select_option('#item-feedback-label', 'false_positive')
        await page.fill('#item-feedback-note', 'Неполное описание')
        await page.click('#item-feedback-save')
        await page.get_by_text('Неполное описание').wait_for()
        await page.goto('http://127.0.0.1:18765/feedback')
        await page.wait_for_function("document.getElementById('feedback-count').textContent === '1'")
        await page.goto('http://127.0.0.1:18765/items')
        await page.wait_for_selector('.product-card')
        opener = page.locator('button[onclick^="openItemModal"]').first
        await opener.click()
        await page.wait_for_selector('#item-modal:not(.hidden)')
        await page.wait_for_function("document.getElementById('item-modal').contains(document.activeElement)")
        await page.keyboard.press('Shift+Tab')
        assert await page.evaluate("document.getElementById('item-modal').contains(document.activeElement)")
        await page.keyboard.press('Escape')
        await page.wait_for_selector('#item-modal.hidden', state='attached')
        assert await opener.evaluate('(el) => document.activeElement === el')
        favorite = page.locator('button[onclick^="toggleFavorite"]').first
        await favorite.click()
        await page.check('#filter-favorites-only')
        await page.wait_for_function("document.querySelectorAll('.product-card').length === 1")
        await page.uncheck('#filter-favorites-only')
        await page.wait_for_function("document.querySelectorAll('.product-card').length === 24")
        await page.fill('#filter-query', 'кофе')
        await page.wait_for_function("document.querySelectorAll('.product-card').length === 4")
        assert await page.locator('.product-card mark').count() == 4
        page.once('dialog', lambda dialog: dialog.accept('Кофемашины'))
        await page.click('#catalog-saved summary')
        await page.click('button[onclick="saveCurrentFilter()"]')
        await page.get_by_role('option', name='Кофемашины').wait_for(state='attached')
        await page.fill('#filter-query', '')
        await page.wait_for_function("document.querySelectorAll('.product-card').length === 24")
        await page.select_option('#saved-filter-select', label='Кофемашины')
        await page.click('button[onclick="applySavedFilter()"]')
        await page.wait_for_function("document.querySelectorAll('.product-card').length === 4")
        assert 'query=%D0%BA%D0%BE%D1%84%D0%B5' in page.url
        await page.fill('#filter-query', '')
        await page.wait_for_function("document.querySelectorAll('.product-card').length === 24")
        await page.goto('http://127.0.0.1:18765/watchlists')
        await page.wait_for_selector('[data-watchlist-select]')
        await page.fill('#new-watchlist-name', 'Покупки до 40 тысяч')
        await page.click('#create-watchlist-form button')
        await page.get_by_text('Покупки до 40 тысяч', exact=False).first.wait_for()
        await page.goto('http://127.0.0.1:18765/items')
        await page.wait_for_selector('.product-card')
        await page.locator('button[onclick^="openItemModal"]').first.click()
        await page.wait_for_selector('#item-modal:not(.hidden)')
        await page.select_option('#item-watchlist-select', label='Покупки до 40 тысяч')
        await page.wait_for_function("!document.getElementById('item-watch-save').disabled")
        await page.fill('#item-watch-target', '40000')
        await page.fill('#item-watch-note', 'Проверить комплект')
        await page.click('button[onclick="saveItemWatchState()"]')
        await page.wait_for_function("document.getElementById('item-watch-status').textContent.replace(/\\s/g, '').includes('40000')")
        await page.keyboard.press('Escape')
        await page.goto('http://127.0.0.1:18765/watchlists')
        await page.wait_for_selector('[data-watchlist-select]')
        await page.get_by_text('Покупки до 40 тысяч', exact=False).first.click()
        await page.get_by_text('Проверить комплект').wait_for()
        await page.goto('http://127.0.0.1:18765/items')
        await page.wait_for_selector('.product-card')
        async with page.expect_download() as download:
            await page.click('button[onclick="exportData(\'csv\')"]')
        assert (await download.value).suggested_filename.endswith('.csv')
        await page.fill('#filter-query', 'fixture no matching item')
        await page.wait_for_timeout(700)
        assert await page.locator('.product-card').count() == 0
        await page.screenshot(path=str(destination / 'catalog-empty.png'), full_page=True)
        await page.route('**/api/items?*', lambda route: route.fulfill(status=500, content_type='application/json',body='{"detail":"fixture error"}'))
        await page.fill('#filter-query','')
        await page.wait_for_selector('#catalog-error:not([hidden])')
        await page.screenshot(path=str(destination/'catalog-error.png'),full_page=True)
        await page.unroute('**/api/items?*')
        await page.click('#catalog-error button')
        await page.wait_for_function("document.querySelectorAll('.product-card').length === 24")
        async def malicious_text(route):
            response=await route.fetch()
            data=await response.json()
            data['items'][0]['title']='<img src=x onerror="window.fixtureXSS=1"> Очень длинный русский заголовок ' * 8
            await route.fulfill(response=response,json=data)
        await page.route('**/api/items?*',malicious_text)
        await page.reload()
        await page.wait_for_selector('.product-card')
        assert await page.evaluate('window.fixtureXSS === undefined')
        assert '<img src=x' in await page.locator('.product-card').first.inner_text()
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.screenshot(path=str(destination/'catalog-long-text.png'),full_page=True)
        await page.unroute('**/api/items?*')
        await page.goto('http://127.0.0.1:18765/monitoring')
        await page.click('button[onclick="openAddSearchModal()"]')
        await page.fill('#modal-name','Browser fixture search')
        await page.fill('#modal-url','https://www.avito.ru/fixture')
        await page.uncheck('#modal-enabled')
        await page.click('#search-modal-form button[type="submit"]')
        await page.wait_for_selector('#search-modal.hidden',state='attached')
        await page.get_by_text('Browser fixture search',exact=True).wait_for()
        # No network to Avito: UI receives explicit blocked/manual/cancelled snapshots.
        await page.goto('http://127.0.0.1:18765/parser')
        state={'job_id':'fixture-job','url':'https://www.avito.ru/fixture','max_pages':3,'status':'blocked','outcome':'blocked','current_page':1,'items_found':1,'saved_count':1,'total_found':1,'new_items_count':1,'save_to_db':True,'progress_pct':33,'elapsed_sec':5,'start_time':time.time()-5,'logs':['Fixture block'],'items':[{'id':'fixture-safe','title':'<script>alert(1)</script>','price':0,'url':'javascript:alert(1)','main_image':'javascript:alert(1)'}],'errors':['Авито ограничил доступ'],'access_message':'Повторная проверка доступа','repeated_captcha':True}
        request_payloads=[]
        async def start_fixture(route):
            request_payloads.append(route.request.post_data_json)
            await route.fulfill(content_type='application/json',body='{"job_id":"fixture-job","status":"started"}')
        await page.route('**/api/parser/start',start_fixture)
        await page.route('**/api/parser/jobs/fixture-job',lambda route: route.fulfill(content_type='application/json',body=json.dumps(state)))
        await page.fill('#parser-url','Samsung Galaxy S25')
        await page.click('#btn-start-parse')
        await page.wait_for_function("document.getElementById('live-status-text').textContent.includes('ограничил')")
        assert request_payloads[0]['url']=='https://www.avito.ru/rossiya?q=Samsung%20Galaxy%20S25'
        assert '<script>alert(1)</script>' in await page.locator('#parser-result-items').text_content()
        assert await page.locator('#parser-result-items script').count()==0
        assert await page.locator('#parser-result-items a').get_attribute('href')=='#'
        assert await page.locator('#parser-access-panel').is_visible()
        await page.screenshot(path=str(destination/'parser-blocked.png'),full_page=True)
        await page.reload()
        await page.wait_for_function("document.getElementById('live-saved-val').textContent==='1'")
        manual=dict(state,job_id='human-job',status='waiting_human',outcome=None,repeated_captcha=False,waiting_until=time.time()+300,access_message='Пройдите проверку в открытом окне',headless=False)
        await page.route('**/api/parser/jobs/fixture-job/retry-human',lambda route: route.fulfill(content_type='application/json',body='{"job_id":"human-job","status":"started"}'))
        await page.route('**/api/parser/jobs/human-job',lambda route: route.fulfill(content_type='application/json',body=json.dumps(manual)))
        await page.route('**/api/parser/attention',lambda route: route.fulfill(content_type='application/json',body=json.dumps({'jobs':[{'job_id':'human-job','status':'waiting_human','message':'Пройдите ручную проверку'}],'searches':[]})))
        await page.click('#parser-human')
        await page.wait_for_function("document.getElementById('live-status-text').textContent.includes('ручной')")
        await page.wait_for_selector('#access-attention:not([hidden])')
        assert await page.locator('#parser-human').is_hidden()
        await page.screenshot(path=str(destination/'parser-waiting-human.png'),full_page=True)
        async def cancel_fixture(route):
            manual.update(status='cancelled',outcome='cancelled',errors=['Сбор остановлен. Уже собранные страницы сохранены.'])
            await route.fulfill(content_type='application/json',body='{"status":"cancelled"}')
        await page.route('**/api/parser/human-job/cancel',cancel_fixture)
        await page.click('#parser-cancel')
        await page.wait_for_function("document.getElementById('live-status-text').textContent==='Сбор остановлен'")
        assert await page.locator('#btn-start-parse').is_enabled()
        await page.unroute('**/api/parser/attention')
        await page.unroute('**/api/parser/start')
        await page.evaluate("sessionStorage.removeItem('avito-parser-job')")
        for role in ('viewer','operator'):
            context=await browser.new_context()
            role_page=await context.new_page()
            await role_page.goto('http://127.0.0.1:18765/login')
            await role_page.fill('#input-username',role)
            await role_page.fill('#input-password','fixture-password-2026')
            await role_page.click('#btn-login-submit')
            await role_page.wait_for_url('http://127.0.0.1:18765/')
            assert await role_page.locator('a.nav-item[href="/settings"]').count()==0
            denied=await role_page.request.get('http://127.0.0.1:18765/api/admin/users')
            assert denied.status==403
            if role=='viewer':
                await role_page.wait_for_function("document.getElementById('metric-items').textContent.trim() === '24'")
                assert await role_page.locator('#overview-toggle').is_disabled()
                await role_page.goto('http://127.0.0.1:18765/items')
                await role_page.wait_for_selector('.product-card')
                assert await role_page.locator('button[onclick^="toggleFavorite"]').first.is_disabled()
                assert await role_page.locator("button[onclick='saveCurrentFilter()']").count() == 0
                await role_page.goto('http://127.0.0.1:18765/watchlists')
                assert await role_page.locator('#create-watchlist-form').count() == 0
                await role_page.goto('http://127.0.0.1:18765/parser')
                assert await role_page.locator('#btn-start-parse').is_disabled()
            await context.close()
        assert not errors, errors
        await browser.close()
    print('PASS: 13 responsive screens (360/768/1440), login lockout, menu keyboard, cards/table, search, saved filters, feedback, watchlists, resale calculator, themes, dialogs, export, error/retry, blocked/manual/cancelled job recovery, XSS text, roles; no browser errors')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    asyncio.run(run(parser.parse_args().out))
