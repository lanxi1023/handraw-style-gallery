import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright
from test_accounts_live import credentials

ROOT=Path(__file__).resolve().parent
URL='https://www.lanxi1023.com/studio/?v=5442bf4d'


async def login(page, name, password):
    await page.locator('#remoteUser').fill(name)
    await page.locator('#remotePassword').fill(password)
    async with page.expect_response(lambda r:'/api/jobs' in r.url and r.request.method=='GET' and r.status==200, timeout=60000):
        await page.locator('#remoteSubmit').click()
    await page.locator('main').wait_for(state='visible',timeout=60000)
    assert await page.locator('#remoteAccount').inner_text()=='账号 '+name


async def logout(page):
    await page.locator('#remoteLogout').click()
    await page.locator('#remoteUser').wait_for(state='visible',timeout=30000)
    assert await page.locator('main').is_hidden()


async def main():
    passwords=credentials()
    record=json.loads((ROOT/'data/account-isolation-live-check.json').read_text())
    async with async_playwright() as p:
        exe=next(Path('E:/号池/runtime/VirtualBrowser/VirtualBrowser').glob('*/VirtualBrowser.exe'))
        browser=await p.chromium.launch(headless=True,executable_path=str(exe))
        for name in passwords:
            context=await browser.new_context(viewport={'width':1440,'height':1400})
            page=await context.new_page();errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            await page.goto(URL,wait_until='domcontentloaded')
            await login(page,name,passwords[name])
            if name=='02' and not record.get('cleaned'):
                await page.locator('#jobs [data-preview="'+record['job_id']+'"]').wait_for(timeout=60000)
                await page.wait_for_function("document.querySelector('#jobs img').naturalWidth > 0")
                await page.locator('#jobs img').first.evaluate('(i)=>i.decode()')
                await page.screenshot(path=str(ROOT/'data/verification/account-02-isolated.png'))
            else:
                assert await page.locator('#jobs [data-preview="'+record['job_id']+'"]').count()==0
                if name!='01':
                    assert await page.locator('#jobs .job').count()==0
                    assert await page.locator('#history option').count()==1
                if name=='02':
                    await page.screenshot(path=str(ROOT/'data/verification/account-02-isolated.png'))
            assert not errors,errors
            await logout(page);await context.close()
            print('Published account '+name+' login and isolated history passed.',flush=True)
        # A shared browser must never migrate 01's legacy draft into a new account.
        context=await browser.new_context(viewport={'width':390,'height':844})
        page=await context.new_page();await page.goto(URL,wait_until='domcontentloaded')
        await page.evaluate("localStorage.setItem('newStudioDrafts',JSON.stringify({general:{prompt:'LEGACY-PRIVATE-01'}}))")
        await login(page,'02',passwords['02'])
        assert await page.locator('#prompt').input_value()==''
        await page.locator('#prompt').fill('PRIVATE-DRAFT-02');await logout(page)
        await login(page,'01',passwords['01'])
        assert await page.locator('#prompt').input_value()=='LEGACY-PRIVATE-01'
        await page.locator('#prompt').fill('PRIVATE-DRAFT-01');await logout(page)
        await login(page,'02',passwords['02'])
        assert await page.locator('#prompt').input_value()=='PRIVATE-DRAFT-02'
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        second=await context.new_page();await second.goto(URL,wait_until='domcontentloaded')
        await second.locator('main').wait_for(state='visible',timeout=60000)
        await logout(page)
        await second.locator('#remoteUser').wait_for(state='visible',timeout=30000)
        assert await second.locator('main').is_hidden()
        assert await second.locator('#jobs .job').count()==0
        print('Shared-browser draft isolation, legacy migration, cross-tab logout and mobile layout passed.',flush=True)
        await context.close();await browser.close()


asyncio.run(main())
