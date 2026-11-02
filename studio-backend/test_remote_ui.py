import asyncio
import hashlib
import json
import tempfile
from pathlib import Path

import httpx
from playwright.async_api import async_playwright
import remote_gateway as gateway
from build_remote import OUT, build


async def main():
    build(ready=True)
    with tempfile.TemporaryDirectory() as tmp:
        gateway.DATA = Path(tmp)
        gateway.CONFIG = gateway.DATA / 'config.json'
        gateway.AUTH = gateway.DATA / 'auth.json'
        gateway.DB = gateway.DATA / 'sessions.db'
        bridge = Path(__file__).resolve().parent / 'data/bridge-secret.txt'
        (gateway.DATA / 'bridge-secret.txt').write_text(bridge.read_text(encoding='ascii') if bridge.exists() else '00' * 32, encoding='ascii')
        gateway.CONFIG.write_text(json.dumps({'enabled': True, 'api_origin': 'https://api.lanxi1023.com', 'web_origins': ['https://www.lanxi1023.com']}))
        salt = b'\x00' * 16
        gateway.AUTH.write_text(json.dumps({'username': 'studio', 'salt': salt.hex(), 'hash': hashlib.scrypt(b'test-only-pass', salt=salt, n=16384, r=8, p=1).hex()}))
        async with async_playwright() as p:
            exe = next(Path('E:/号池/runtime/VirtualBrowser/VirtualBrowser').glob('*/VirtualBrowser.exe'))
            browser = await p.chromium.launch(headless=True, executable_path=str(exe))
            for width, height in [(1440, 1000), (390, 844)]:
                context = await browser.new_context(viewport={'width': width, 'height': height}, accept_downloads=True)
                page = await context.new_page()
                errors, bad_requests = [], []
                page.on('pageerror', lambda e: errors.append(str(e)))

                async def web(route):
                    path = route.request.url.split('www.lanxi1023.com', 1)[-1].split('?', 1)[0]
                    if not path.startswith('/studio/'):
                        bad_requests.append(path)
                        await route.fulfill(status=404)
                        return
                    relative = path[len('/studio/'):] or 'index.html'
                    target = OUT / relative
                    if not target.is_file():
                        bad_requests.append(path)
                        await route.fulfill(status=404)
                        return
                    mime = {'.js': 'application/javascript', '.css': 'text/css', '.html': 'text/html'}.get(target.suffix, 'application/octet-stream')
                    await route.fulfill(status=200, body=target.read_bytes(), content_type=mime)

                async def api(route):
                    request = route.request
                    headers = await request.all_headers()
                    headers.pop('content-length', None)
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway.app)) as client:
                        r = await client.request(request.method, request.url, headers=headers, content=request.post_data_buffer)
                    await route.fulfill(status=r.status_code, headers=dict(r.headers), body=r.content)

                await page.route('https://www.lanxi1023.com/**', web)
                await page.route('https://api.lanxi1023.com/**', api)
                await page.goto('https://www.lanxi1023.com/studio/', wait_until='domcontentloaded')
                await page.locator('#remoteUser').fill('studio')
                await page.locator('#remotePassword').fill('test-only-pass')
                await page.locator('#remoteSubmit').click()
                await page.locator('#history option[value="all"]').wait_for(state='attached',timeout=30000)
                await page.locator('#history').select_option('all')
                await page.locator('#jobs [data-preview]').first.wait_for(timeout=30000)
                assert await page.locator('#remoteGate').is_hidden()
                await page.locator('#jobs img').first.wait_for()
                await page.wait_for_function("document.querySelector('#jobs img').naturalWidth > 0")
                await page.locator('#openDownload').click()
                await page.locator('#downloadSelectAll').uncheck()
                await page.locator('#downloadGrid input').first.check()
                async with page.expect_download() as pending:
                    await page.locator('#downloadSelected').click()
                download = await pending.value
                assert await download.path()
                await page.locator('#closeDownload').click()
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert await page.locator('a[href="/open-pool"]').count() == 0
                assert not errors, errors
                assert not bad_requests, bad_requests
                if width == 1440:
                    async def offline(route):
                        await route.abort()
                    await page.route('https://api.lanxi1023.com/api/health', offline)
                    await page.evaluate("studioFetch('/api/health').catch(() => null)")
                    assert await page.locator('#remoteConnection').inner_text() == '生图电脑暂时离线'
                    await page.unroute('https://api.lanxi1023.com/api/health', offline)
                    await page.evaluate("studioFetch('/api/batches')")
                    assert await page.locator('#remoteConnection').inner_text() == '已连接生图电脑'
                await page.locator('#remoteLogout').click()
                await page.locator('#remotePassword').wait_for(state='visible')
                assert await page.locator('main').is_hidden()
                print(f'Remote login, assets, images, selected ZIP download, logout and layout passed at {width}px')
                await context.close()
            await browser.close()


asyncio.run(main())
