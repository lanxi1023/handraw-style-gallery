"""Decode real public-gallery images; no image-generation requests."""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parent


async def main():
    url=(ROOT/'data/quick-link-01.txt').read_text(encoding='utf-8').strip()
    async with async_playwright() as p:
        exe=next(Path('E:/\u53f7\u6c60/runtime/VirtualBrowser/VirtualBrowser').glob('*/VirtualBrowser.exe'))
        browser=await p.chromium.launch(headless=True,executable_path=str(exe))
        for width,height in [(1440,1100),(390,844)]:
            context=await browser.new_context(viewport={'width':width,'height':height})
            page=await context.new_page()
            await page.goto(url,wait_until='domcontentloaded')
            await page.locator('main').wait_for(state='visible',timeout=45000)
            await page.locator('[data-mode=portrait]').click()
            for kind in ('outfit','pose','face'):
                await page.locator('[data-combo="0"][data-kind="'+kind+'"]').click()
                await decoded(page)
                await page.locator('#closePicker').click()
                print('Public gallery images decoded:',width,kind,flush=True)
            await page.locator('.portrait-style summary').first.click()
            await page.locator('[data-combo="0"][data-kind=style]').click()
            for group in ('FA','FB','FC','FD','FE','FF','FG','FH','FI'):
                await page.locator('[data-style-group="'+group+'"]').click()
                await decoded(page)
            if width==1440:
                await page.locator('[data-style-group=FA]').click()
                await decoded(page)
                await page.screenshot(path=str(ROOT/'data/verification/style-gallery-fixed.jpg'))
            await page.locator('#closePicker').click()
            print('Public gallery images decoded:',width,'all nine style categories',flush=True)
            await page.locator('[data-mode=handdraw]').click()
            for kind in ('layout','color'):
                await page.locator('[data-picker="'+kind+'"]').click()
                await decoded(page)
                await page.locator('#closePicker').click()
                print('Public gallery images decoded:',width,kind,flush=True)
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            await context.close()
        await browser.close()


async def decoded(page):
    await page.wait_for_function('''() => {
        const images=[...document.querySelectorAll('#pickerGrid img')].slice(0,2);
        return images.length===2 && images.every(i=>i.complete && i.naturalWidth>0);
    }''',timeout=30000)


if __name__=='__main__':asyncio.run(main())
