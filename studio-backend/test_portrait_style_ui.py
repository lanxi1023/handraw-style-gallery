import asyncio
from pathlib import Path
from playwright.async_api import async_playwright


async def check(page,width):
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    await page.goto('http://127.0.0.1:8768/',wait_until='domcontentloaded')
    await page.locator('[data-mode=portrait]').click()
    await page.locator('#count').fill('2')
    await page.wait_for_function('styles.length > 0')
    assert await page.locator('.portrait-style').count()==2
    assert not await page.locator('.portrait-style').first.evaluate('(x)=>x.open')
    await page.locator('#draw').click()
    field=page.locator('[data-combo-prompt="0"]')
    await field.fill((await field.input_value())+'\n自定义背景：纯白色')
    await page.locator('.portrait-style summary').first.click()
    await page.locator('[data-combo="0"][data-kind=style]').click()
    await page.locator('[data-style-group=FA]').click()
    await page.locator('[data-choice="FA-036"]').click()
    await page.wait_for_function("document.querySelector('[data-combo-prompt=\"0\"]').value.includes('第 4 张图片仅作画风参考。')")
    assert await page.locator('.portrait-style-choice img').count()==1
    assert '自定义背景：纯白色' in await field.input_value()
    assert '自然真实的人物写真' not in await field.input_value()
    items=await page.evaluate('makeItems()')
    assert len(items)==2 and items[0]['style']=='FA-036' and not items[1]['style']
    preview=await page.request.post('http://127.0.0.1:8768/api/compile',data=items[0])
    assert preview.status==200 and (await preview.json())['reference_count']==4
    await page.locator('[data-remove-combo="0"][data-kind=pose]').click()
    await page.wait_for_function("document.querySelector('[data-combo-prompt=\"0\"]').value.includes('第 3 张图片仅作画风参考。')")
    assert '第 4 张图片仅作画风参考。' not in await field.input_value()
    await page.locator('.portrait-style summary').first.click()
    assert not await page.locator('.portrait-style').first.evaluate('(x)=>x.open')
    assert (await page.evaluate('makeItems()'))[0]['style']=='FA-036'
    await page.reload(wait_until='domcontentloaded')
    await page.wait_for_function('styles.length > 0')
    assert not await page.locator('.portrait-style').first.evaluate('(x)=>x.open')
    assert (await page.evaluate('makeItems()'))[0]['style']=='FA-036'
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    if width==1440:
        await page.locator('.portrait-style summary').first.click()
        await page.locator('.combo').first.scroll_into_view_if_needed()
        await page.screenshot(path=str(Path(__file__).parent/'data/verification/portrait-optional-style.png'),full_page=False)
        await page.locator('[data-clear-portrait-style="0"]').click()
        text=await field.input_value()
        assert '【写真画风】' not in text and '自定义背景：纯白色' in text
        assert (await page.evaluate('makeItems()'))[0]['style'] is None
    await page.locator('[data-mode=handdraw]').click()
    assert await page.locator('#styleLabel').inner_text()=='选择画风 +'
    assert not errors,errors
    print('Portrait optional style UI, prompt, reference positions, draft isolation:',width,'passed')


async def main():
    async with async_playwright() as p:
        exe=next(Path('E:/\u53f7\u6c60/runtime/VirtualBrowser/VirtualBrowser').glob('*/VirtualBrowser.exe'))
        browser=await p.chromium.launch(headless=True,executable_path=str(exe))
        for width,height in [(1440,1000),(390,844)]:
            page=await browser.new_page(viewport={'width':width,'height':height})
            await check(page,width)
            await page.close()
        await browser.close()


asyncio.run(main())
