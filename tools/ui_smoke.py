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
            for path, name in (('/', 'dashboard'), ('/items', 'catalog'), ('/monitoring', 'monitoring'),
                               ('/settings', 'settings'), ('/parser', 'parser'), ('/logs', 'logs')):
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
        await page.fill('#filter-query', 'fixture no matching item')
        await page.wait_for_timeout(700)
        assert await page.locator('.product-card').count() == 0
        await page.screenshot(path=str(destination / 'catalog-empty.png'), full_page=True)
        assert not errors, errors
        await browser.close()
    print('PASS: responsive screens, catalogue filtering, and no browser errors')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    asyncio.run(run(parser.parse_args().out))
