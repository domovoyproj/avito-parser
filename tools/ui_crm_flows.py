"""CRM user journeys on the isolated fixture only; no live records or sends."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

BASE='http://127.0.0.1:18765'


async def check_crm(page, destination=None):
    await page.goto(BASE+'/crm')
    await page.locator('#crm-metrics strong').first.wait_for()
    async def submit(form, endpoint, method):
        async with page.expect_response(lambda r: r.url==BASE+endpoint and r.request.method==method) as pending:
            await page.locator(form+' button[type=submit]').click()
        response=await pending.value
        assert response.ok,(endpoint,await response.text())
        return await response.json()
    await page.click('[data-tab=contacts]')
    await page.click('#crm-contact-add')
    await page.fill('#crm-contact-form [name=name]','CRM browser buyer')
    await page.fill('#crm-contact-form [name=phone]','fixture phone')
    contact=await submit('#crm-contact-form','/api/crm/contacts','POST')
    await page.locator('#crm-dialog').wait_for(state='hidden')
    await page.click('[data-tab=deals]')
    await page.click('#crm-create')
    await page.fill('#crm-deal-form [name=title]','CRM browser phone <script>window.crmXss=1</script>')
    await page.select_option('#crm-deal-form [name=stage]','purchased')
    await page.fill('#crm-deal-form [name=purchase]','12000')
    await page.fill('#crm-deal-form [name=target]','18000')
    await page.select_option('#crm-deal-form [name=buyer_id]',str(contact['id']))
    deal=await submit('#crm-deal-form','/api/crm/deals','POST')
    did=deal['id']
    await page.locator('#crm-expense-form').wait_for()
    await page.fill('#crm-expense-form [name=amount]','1500.50')
    await page.fill('#crm-expense-form [name=note]','CRM repair')
    await submit('#crm-expense-form',f'/api/crm/deals/{did}/expenses','POST')
    await page.wait_for_function("document.querySelector('.crm-ledger').textContent.includes('4')")
    await page.select_option('#crm-deal-form [name=stage]','sold')
    await page.fill('#crm-deal-form [name=sale]','18000')
    sold=await submit('#crm-deal-form',f'/api/crm/deals/{did}','PUT')
    assert sold['profit']==449950
    assert sold['buyer_id']==contact['id']
    await page.wait_for_function("document.querySelector('.crm-ledger').textContent.includes('4')")
    await page.click('#crm-deal-task')
    await page.fill('#crm-task-form [name=title]','CRM buyer follow-up')
    await page.fill('#crm-task-form [name=due_at]','2027-01-01T12:00')
    await page.select_option('#crm-task-form [name=owner_id]',label='operator')
    task=await submit('#crm-task-form','/api/crm/tasks','POST')
    assert task['deal_id']==did
    await page.locator('#crm-dialog').wait_for(state='hidden')
    await page.click('[data-tab=tasks]')
    await page.click(f'[data-task="{task["id"]}"]')
    await page.select_option('#crm-task-form [name=done]','1')
    completed=await submit('#crm-task-form',f'/api/crm/tasks/{task["id"]}','PUT')
    assert completed['done']
    await page.locator('#crm-dialog').wait_for(state='hidden')
    await page.click('[data-tab=deals]')
    await page.reload()
    await page.locator(f'[data-deal="{did}"]').wait_for()
    assert await page.evaluate('window.crmXss===undefined')
    # Old data must not silently overwrite a colleague's edit.
    await page.click(f'[data-deal="{did}"]')
    await page.locator('#crm-expense-form').wait_for()
    await page.evaluate("""async(id)=>{const d=await apiFetch('/api/crm/deals/'+id);const names=['title','stage','purchase','target','sale','owner_id','supplier_id','buyer_id','source_url','listing_url','notes','serial','item_id','archived','version'];const body=Object.fromEntries(names.map(k=>[k,d[k]]));body.notes='Colleague changed';await apiFetch('/api/crm/deals/'+id,{method:'PUT',body:JSON.stringify(body)});} """,did)
    await page.fill('#crm-deal-form [name=notes]','Old draft')
    async with page.expect_response(lambda r:r.url==BASE+f'/api/crm/deals/{did}' and r.request.method=='PUT') as conflict:
        await page.click('#crm-deal-form button[type=submit]')
    assert (await conflict.value).status==409
    await page.locator('#crm-dialog-error').wait_for()
    assert 'коллега' in await page.text_content('#crm-dialog-error')
    await page.click('#crm-close')
    await page.click(f'[data-deal="{did}"]')
    await page.wait_for_function("document.querySelector('#crm-deal-form [name=notes]').value==='Colleague changed'")
    if destination:
        await page.screenshot(path=str(destination/'crm-deal-1440.png'),full_page=True)
    await page.click('#crm-close')
    for width in (1440,768,360):
        await page.set_viewport_size({'width':width,'height':1000})
        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
        if destination:await page.screenshot(path=str(destination/f'crm-{width}.png'),full_page=True)
    await page.set_viewport_size({'width':1440,'height':1000})
    # Catalogue import preserves the source item and proposed purchase amount.
    await page.goto(BASE+'/crm?item=fixture-0')
    await page.locator('#crm-deal-form').wait_for()
    assert 'MacBook' in await page.input_value('#crm-deal-form [name=title]')
    assert float(await page.input_value('#crm-deal-form [name=purchase]'))==15000
    await page.click('#crm-close')


async def main():
    destination=Path('ui-artifacts/crm');destination.mkdir(parents=True,exist_ok=True)
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page(viewport={'width':1440,'height':1000})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto(BASE+'/login')
        await page.fill('#input-username','admin');await page.fill('#input-password','fixture-password-2026')
        await page.click('#btn-login-submit');await page.wait_for_url(BASE+'/')
        await check_crm(page,destination)
        assert not errors,errors
        await browser.close()
    print('CRM browser flow PASS')


if __name__=='__main__':asyncio.run(main())
