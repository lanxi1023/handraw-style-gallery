"""Verify the private link with fresh browsers, without submitting image jobs."""
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright
from build_remote import OUT,build

ROOT=Path(__file__).resolve().parent


async def main():
    build(ready=True)
    private_url=(ROOT/'data/quick-link-01.txt').read_text(encoding='utf-8').strip()
    token=private_url.split('#access=',1)[1]
    async with async_playwright() as p:
        exe=next(Path('E:/\u53f7\u6c60/runtime/VirtualBrowser/VirtualBrowser').glob('*/VirtualBrowser.exe'))
        browser=await p.chromium.launch(headless=True,executable_path=str(exe))
        for width,height in [(1440,1000),(390,844)]:
            context=await browser.new_context(viewport={'width':width,'height':height})
            page=await context.new_page();urls=[];errors=[]
            page.on('request',lambda r:urls.append(r.url))
            page.on('pageerror',lambda e:errors.append(type(e).__name__))
            async def web(route):
                path=urlsplit(route.request.url).path
                if not path.startswith('/studio/'):
                    await route.fulfill(status=404);return
                relative=path[len('/studio/'):] or 'index.html'
                target=OUT/relative
                if not target.is_file():
                    await route.fulfill(status=404);return
                mime={'.js':'application/javascript','.css':'text/css','.html':'text/html'}.get(target.suffix,'application/octet-stream')
                await route.fulfill(status=200,body=target.read_bytes(),content_type=mime)
            await page.route('https://www.lanxi1023.com/**',web)
            await page.goto(private_url,wait_until='domcontentloaded')
            await page.locator('main').wait_for(state='visible',timeout=30000)
            assert await page.locator('#remoteAccount').inner_text()=='账号 01'
            assert 'access=' not in page.url and token not in page.url
            assert not any(token in url for url in urls)
            assert token not in await page.evaluate('JSON.stringify(localStorage)')
            assert await page.locator('#remoteGate').is_hidden()
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            print('Fresh shortcut entry and credential handling passed at',width,flush=True)
            await page.locator('#remoteLogout').click()
            await page.locator('#remotePassword').wait_for(state='visible',timeout=30000)
            assert await page.locator('main').is_hidden()
            await page.goto(private_url,wait_until='domcontentloaded')
            await page.locator('main').wait_for(state='visible',timeout=30000)
            assert await page.locator('#remoteAccount').inner_text()=='账号 01'
            assert not errors,errors
            print('Private shortcut: fresh-browser entry, owner, hidden credential, logout, reuse and layout passed at',width)
            await context.close()
        await browser.close()


if __name__=='__main__':asyncio.run(main())
