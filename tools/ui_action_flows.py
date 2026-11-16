"""Action-to-result browser regressions on the isolated UI fixture only."""
import asyncio
import time

BASE = 'http://127.0.0.1:18765'


async def check_actions(page):
    async def get(path):
        response = await page.request.get(BASE + path)
        assert response.ok, (path, response.status)
        return await response.json()

    async def click_response(selector, path, method='POST'):
        async with page.expect_response(lambda r: r.url == BASE + path and r.request.method == method) as response:
            await page.click(selector)
        return await response.value

    # Search editing/toggling must change the intended row, not open another flow.
    await page.goto(BASE + '/monitoring')
    searches = await get('/api/searches')
    if not any(item['name'] == 'Browser fixture search' for item in searches):
        await page.click('button[onclick="openAddSearchModal()"]')
        await page.fill('#modal-name', 'Browser fixture search')
        await page.fill('#modal-url', 'https://www.avito.ru/fixture')
        await page.uncheck('#modal-enabled')
        assert (await click_response('#search-modal-form button[type="submit"]', '/api/searches')).ok
        await page.wait_for_selector('#search-modal.hidden', state='attached')
        searches = await get('/api/searches')
    search = next(item for item in searches if item['name'] == 'Browser fixture search')
    sid = search['id']
    await page.click(f'button[onclick="openEditSearchModal({sid})"]')
    await page.fill('#modal-name', 'Edited browser fixture search')
    await page.fill('#modal-min-price', '0')
    await page.fill('#modal-max-price', '40000')
    await page.fill('#modal-interval', '17')
    response = await click_response('#search-modal-form button[type="submit"]', f'/api/searches/{sid}', 'PUT')
    assert response.ok
    await page.wait_for_selector('#search-modal.hidden', state='attached')
    saved = next(item for item in await get('/api/searches') if item['id'] == sid)
    assert (saved['name'], saved['min_price'], saved['max_price'], saved['check_interval_min']) == ('Edited browser fixture search', 0, 40000, 17)
    for enabled in (True, False):
        response = await click_response(f'button[onclick="toggleSearchItem({sid})"]', f'/api/searches/{sid}/toggle', 'PATCH')
        assert response.ok
        saved = next(item for item in await get('/api/searches') if item['id'] == sid)
        assert saved['enabled'] == enabled
        await page.locator(f'button[onclick="toggleSearchItem({sid})"]').filter(has_text='АКТИВЕН' if enabled else 'ПАУЗА').wait_for()
    await page.click(f'button[onclick="openEditSearchModal({sid})"]')
    assert await page.input_value('#modal-min-price') == '0'
    await page.fill('#modal-url', 'https://example.com/not-avito')
    response = await click_response('#search-modal-form button[type="submit"]', f'/api/searches/{sid}', 'PUT')
    assert response.status == 400
    assert await page.locator('#search-modal').is_visible()
    assert next(item for item in await get('/api/searches') if item['id'] == sid)['url'] == search['url']
    await page.fill('#modal-url', search['url'])
    await page.fill('#modal-min-price', '200')
    await page.fill('#modal-max-price', '100')
    response = await click_response('#search-modal-form button[type="submit"]', f'/api/searches/{sid}', 'PUT')
    assert response.status == 422
    await page.get_by_text('Минимальная цена не должна превышать максимальную', exact=False).wait_for()
    await page.locator('#search-modal button[onclick="closeSearchModal()"]').first.click()

    # Both access buttons must POST a headed-browser check before navigating.
    alert = dict(search_id=sid, url=search['url'], name=search['name'], message='Fixture access check')
    attention = {'jobs': [], 'searches': [alert]}
    state = dict(job_id='action-manual', url=search['url'], max_pages=1, status='waiting_human',
                 outcome=None, headless=False, save_to_db=False, current_page=0, total_found=0,
                 start_time=time.time(), waiting_until=time.time()+300, logs=[], errors=[], items=[])
    starts, retries = [], []
    async def start(route):
        starts.append(route.request.post_data_json)
        await asyncio.sleep(.2)
        await route.fulfill(json={'job_id':'action-manual', 'status':'started'})
    async def status(route):
        response = await route.fetch()
        data = await response.json()
        data['access_alerts'] = [alert]
        await route.fulfill(response=response, json=data)
    await page.route('**/api/parser/attention', lambda route: route.fulfill(json=attention))
    await page.route('**/api/monitoring/status', status)
    await page.route('**/api/parser/start', start)
    await page.route('**/api/parser/jobs/action-manual', lambda route: route.fulfill(json=state))
    for selector in ('#access-attention-link', f'.access-inline-link[onclick="checkSearchAccess({sid}, this)"]',
                     f'button[title="Ручная проверка доступа"][onclick="checkSearchAccess({sid}, this)"]'):
        await page.goto(BASE + '/monitoring')
        await page.wait_for_selector(selector + ':not([disabled])')
        previous = len(starts)
        await page.locator(selector).evaluate('(button) => {button.click(); button.click();}')
        await page.wait_for_url(BASE + '/parser?job=action-manual')
        await page.wait_for_function("document.getElementById('live-status-text').textContent.includes('ручной')")
        assert len(starts) == previous + 1, 'double click started duplicate jobs'
        assert starts[-1] == dict(url=search['url'], engine='playwright', headless=False, max_pages=1, save_to_db=False)
        assert await page.locator('#parser-access-panel').is_visible()
        await page.reload()
        await page.wait_for_function("document.getElementById('live-status-text').textContent.includes('ручной')")
        assert len(starts) == previous + 1, 'reload started another browser'
    # Existing check is reused by the row button as well as by the banner.
    attention['jobs'] = [dict(job_id='action-manual', url=search['url'], status='waiting_human', message='Fixture wait')]
    await page.goto(BASE + '/monitoring')
    await page.click(f'.access-inline-link[onclick="checkSearchAccess({sid}, this)"]')
    await page.wait_for_url(BASE + '/parser?job=action-manual')
    assert len(starts) == 3
    attention['jobs'] = [dict(job_id='blocked-action', url=search['url'], status='blocked', message='Fixture blocked')]
    async def retry(route):
        retries.append(route.request.method)
        await route.fulfill(json={'job_id':'action-manual', 'status':'started'})
    await page.route('**/api/parser/jobs/blocked-action/retry-human', retry)
    await page.goto(BASE + '/monitoring')
    await page.click('#access-attention-link')
    await page.wait_for_url(BASE + '/parser?job=action-manual')
    assert retries == ['POST'] and len(starts) == 3
    attention['jobs'] = []
    await page.unroute('**/api/parser/start')
    await page.route('**/api/parser/start', lambda route: route.fulfill(status=503, json={'detail':'Fixture unavailable'}))
    await page.goto(BASE + '/monitoring')
    await page.click('#access-attention-link')
    await page.get_by_text('Не удалось открыть окно проверки. Fixture unavailable', exact=False).wait_for()
    assert page.url == BASE + '/monitoring'
    assert await page.locator('#access-attention-link').is_enabled()
    for route in ('**/api/parser/attention', '**/api/monitoring/status', '**/api/parser/start',
                  '**/api/parser/jobs/action-manual', '**/api/parser/jobs/blocked-action/retry-human'):
        await page.unroute(route)
    await page.evaluate("sessionStorage.removeItem('avito-parser-job')")

    # The actual settings form must preserve hidden prompt/key and explicit zeros.
    await page.evaluate("""async () => {
      await apiFetch('/api/ai/settings', {method:'POST', body:JSON.stringify({enabled:true,provider:'openai',model:'gpt-4o-mini',api_base:'https://api.openai.com/v1',api_key:'fixture-action-key',prompt_template:'Keep fixture prompt'})});
      const settings = await apiFetch('/api/settings');
      await apiFetch('/api/settings', {method:'POST', body:JSON.stringify({headless:settings.scraper.headless,timeout_ms:settings.scraper.timeout_ms,page_delay_min:0,page_delay_max:0,proxy_enabled:false})});
    }""")
    await page.goto(BASE + '/settings')
    await page.wait_for_function("document.getElementById('set-ai-model').value === 'gpt-4o-mini'")
    assert await page.input_value('#set-ai-api-key') == ''
    assert await page.input_value('#set-delay-min') == '0'
    assert await page.input_value('#set-delay-max') == '0'
    response = await click_response('button[onclick="saveAllSettings()"]', '/api/ai/settings')
    assert response.ok
    await page.get_by_text('Все параметры (включая AI и Webhook) успешно сохранены', exact=False).wait_for()
    ai = (await get('/api/ai/settings'))['settings']
    assert ai['prompt_template'] == 'Keep fixture prompt' and ai['has_api_key'] and ai['api_key'] == ''

    # Rules: zero remains zero, unsafe-looking labels render as text, deltas affect scores.
    assert (await click_response('#btn-recalc-all', '/api/items/recalculate-scores')).ok
    baseline = (await get('/api/items/fixture-0'))['item']['deal_score']
    label = '<img src=x onerror="window.ruleXSS=1"> Fixture zero'
    async def add_rule(pattern, name, delta):
        await page.fill('#rule-pattern', pattern)
        await page.fill('#rule-label', name)
        await page.fill('#rule-score-delta', str(delta))
        return await click_response('button[onclick="addCustomRule()"]', '/api/scoring/rules')
    assert (await add_rule('MacBook', label, 0)).ok
    await page.locator('#custom-rules-list').get_by_text(label, exact=True).wait_for()
    assert await page.locator('#custom-rules-list img').count() == 0
    assert await page.evaluate('window.ruleXSS === undefined')
    zero = next(rule for rule in (await get('/api/scoring/rules'))['rules'] if rule['label'] == label)
    assert zero['score_delta'] == 0
    response = await add_rule('(', 'Invalid fixture', -7)
    assert response.status == 400
    assert not any(r['label'] == 'Invalid fixture' for r in (await get('/api/scoring/rules'))['rules'])
    assert (await add_rule('MacBook', 'Fixture penalty', -7)).ok
    rule = next(r for r in (await get('/api/scoring/rules'))['rules'] if r['label'] == 'Fixture penalty')
    assert (await click_response('#btn-recalc-all', '/api/items/recalculate-scores')).ok
    assert (await get('/api/items/fixture-0'))['item']['deal_score'] == max(0, baseline - 7)
    assert (await click_response(f'button[onclick="toggleRule({rule["id"]})"]', f'/api/scoring/rules/{rule["id"]}/toggle', 'PATCH')).ok
    assert (await click_response('#btn-recalc-all', '/api/items/recalculate-scores')).ok
    assert (await get('/api/items/fixture-0'))['item']['deal_score'] == baseline
    for rule_id in (zero['id'], rule['id']):
        page.once('dialog', lambda dialog: dialog.accept())
        assert (await click_response(f'button[onclick="deleteRule({rule_id})"]', f'/api/scoring/rules/{rule_id}', 'DELETE')).ok
    assert not (await get('/api/scoring/rules'))['rules']
    await page.goto(BASE + '/monitoring')
    page.once('dialog', lambda dialog: dialog.accept())
    assert (await click_response(f'button[onclick="deleteSearchItem({sid})"]', f'/api/searches/{sid}', 'DELETE')).ok
    assert not any(item['id'] == sid for item in await get('/api/searches'))
    print('PASS: search edit/toggle/delete, both manual-access buttons, duplicate/reload/reuse/retry/error flows, settings round-trip, rule validation/XSS/zero/weights/toggle/delete and actual score recalculation')


if __name__ == '__main__':
    from playwright.async_api import async_playwright

    async def main():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page(viewport={'width':1440, 'height':1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.goto(BASE + '/login')
            await page.fill('#input-username', 'admin')
            await page.fill('#input-password', 'fixture-password-2026')
            await page.click('#btn-login-submit')
            await page.wait_for_url(BASE + '/')
            await check_actions(page)
            assert not errors, errors
            await browser.close()

    asyncio.run(main())
