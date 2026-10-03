"""Browser QA against tools/ui_fixture.py, saving before/after screenshots."""
import argparse
import asyncio
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
        await page.fill('#input-username', 'admin')
        await page.fill('#input-password', 'fixture-password-2026')
        await page.click('#btn-login-submit')
        await page.wait_for_url('http://127.0.0.1:18765/')
        for width in (1440, 768, 360):
            await page.set_viewport_size({'width': width, 'height': 1000})
            for path, name in (('/', 'dashboard'), ('/items', 'catalog'), ('/watchlists', 'watchlists'), ('/monitoring', 'monitoring'),
                               ('/settings', 'settings'), ('/parser', 'parser'), ('/logs', 'logs'), ('/exports', 'exports'),
                               ('/item-parser','item-parser'), ('/price-drops','price-drops'), ('/proxies','proxies'), ('/admin/users','users')):
                await page.goto('http://127.0.0.1:18765' + path)
                await page.wait_for_timeout(500)
                if path == '/':
                    await page.wait_for_function("document.getElementById('metric-items').textContent.trim() === '24'")
                await page.screenshot(path=str(destination / f'{name}-{width}.png'), full_page=True)
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (path, width, 'overflow')
        await page.set_viewport_size({'width': 1440, 'height': 1000})
        await page.goto('http://127.0.0.1:18765/')
        await page.click('.theme-switch')
        await page.reload()
        await page.wait_for_function("document.getElementById('metric-items').textContent.trim() === '24'")
        assert await page.get_attribute('html', 'data-theme') == 'light'
        await page.screenshot(path=str(destination / 'dashboard-light-1440.png'), full_page=True)
        await page.click('.theme-switch')
        await page.goto('http://127.0.0.1:18765/items')
        await page.wait_for_selector('.product-card')
        opener = page.locator('button[onclick^="openItemModal"]').first
        await opener.focus()
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
                await role_page.goto('http://127.0.0.1:18765/watchlists')
                assert await role_page.locator('#create-watchlist-form').count() == 0
            await context.close()
        assert not errors, errors
        await browser.close()
    print('PASS: 12 responsive screens, watchlists, themes, dialogs, search, favorite, export, error/retry, XSS text, roles; no browser errors')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    asyncio.run(run(parser.parse_args().out))
