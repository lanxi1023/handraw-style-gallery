"""Verify the published frontend with the real HTTPS bridge; no new generation."""
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent


async def main():
    lines = (ROOT / 'data/remote-login.txt').read_text(encoding='utf-8').splitlines()
    username = next(x.split('：', 1)[1] for x in lines if x.startswith('用户名：'))
    password = next(x.split('：', 1)[1] for x in lines if x.startswith('密码：'))
    record = json.loads((ROOT / 'data/remote-live-check.json').read_text(encoding='utf-8'))
    async with async_playwright() as p:
        exe = next(Path('E:/号池/runtime/VirtualBrowser/VirtualBrowser').glob('*/VirtualBrowser.exe'))
        browser = await p.chromium.launch(headless=True, executable_path=str(exe))
        for width, height in [(1440, 1000), (390, 844)]:
            context = await browser.new_context(viewport={'width': width, 'height': height}, accept_downloads=True)
            page = await context.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            await page.goto('https://www.lanxi1023.com/studio/?release=affaa326', wait_until='domcontentloaded')
            await page.locator('#remoteSubmit').wait_for(state='visible')
            await page.wait_for_function('!document.querySelector("#remoteSubmit").disabled')
            await page.locator('#remoteUser').fill(username)
            await page.locator('#remotePassword').fill(password)
            await page.locator('#remoteSubmit').click()
            await page.locator('main').wait_for(state='visible', timeout=60000)
            await page.locator('#history option[value="' + record['batch_id'] + '"]').wait_for(state='attached')
            await page.locator('#history').select_option(record['batch_id'])
            await page.locator('#jobs [data-preview="' + record['job_id'] + '"]').wait_for(timeout=60000)
            await page.locator('#jobs [data-preview="' + record['job_id'] + '"]').click()
            await page.wait_for_function('document.querySelector("#previewImage").naturalWidth > 0', timeout=60000)
            await page.locator('#previewImage').evaluate('(image) => image.decode()')
            await page.wait_for_function('''() => {
                const r = document.querySelector('#previewImage').getBoundingClientRect();
                return r.width > 200 && r.height > 200 && Math.abs(r.y + r.height / 2 - innerHeight / 2) < 20;
            }''')
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            if width == 1440:
                (ROOT / 'data/verification').mkdir(exist_ok=True)
                await page.screenshot(path=str(ROOT / 'data/verification/remote-studio-success.png'))
            await page.locator('#closePreview').click()
            await page.locator('#openDownload').click()
            async with page.expect_download(timeout=60000) as pending:
                await page.locator('#downloadSelected').click()
            download = await pending.value
            assert await download.path()
            await page.locator('#closeDownload').click()
            assert not errors, errors
            await page.locator('#remoteLogout').click()
            await page.locator('#remotePassword').wait_for(state='visible')
            assert await page.locator('main').is_hidden()
            print(f'Published HTTPS frontend: login, preview, ZIP, logout and {width}px layout passed.', flush=True)
            await context.close()
        await browser.close()


asyncio.run(main())
