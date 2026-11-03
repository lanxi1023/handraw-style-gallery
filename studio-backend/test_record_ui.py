"""Verify local record controls and details without submitting generation tasks."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parent


async def main():
    async with async_playwright() as p:
        exe=next(Path('E:/\u53f7\u6c60/runtime/VirtualBrowser/VirtualBrowser').glob('*/VirtualBrowser.exe'))
        browser=await p.chromium.launch(headless=True,executable_path=str(exe))
        for width,height in [(1440,1000),(390,844)]:
            page=await browser.new_page(viewport={'width':width,'height':height});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            await page.goto('http://127.0.0.1:8768/',wait_until='domcontentloaded')
            await page.locator('#history option[value=all]').wait_for(state='attached')
            await page.locator('#history').select_option('all')
            await page.wait_for_function("batch==='all' && jobsCache.length>10")
            info=await page.evaluate('''() => {
                const completed=jobsCache.filter(x=>x.state==='completed');
                const failed=jobsCache.findIndex(x=>['failed','uncertain'].includes(x.state));
                return {order:completed.map(x=>x.finished_at||x.created_at),failedLast:failed<0||jobsCache.slice(failed).every(x=>['failed','uncertain'].includes(x.state)),portrait:completed.find(x=>x.config?.mode==='portrait'&&x.config?.outfit)?.id};
            }''')
            assert info['order']==sorted(info['order'],reverse=True) and info['failedLast']
            assert await page.locator('[data-favorite],#onlyFavorites').count()==0
            assert info['portrait']
            await page.locator('[data-preview="'+info['portrait']+'"]').click()
            await page.wait_for_function("document.querySelector('#previewImage').naturalWidth>0")
            image=await page.locator('#previewImage').bounding_box();details=await page.locator('#previewDetails').bounding_box()
            assert 0<=details['x']-image['x']<=12 and 0<=details['y']-image['y']<=12,(image,details)
            await page.locator('#previewDetails').click()
            await page.locator('#jobDetails .job-detail-fields').wait_for()
            text=await page.locator('#jobDetailsContent').inner_text()
            assert '衣服编号' in text and '姿势编号' in text and '脸部编号' in text and '本次实际提示词' in text
            assert '历史记录未保存编号' not in text,text
            assert 'gpt-image2-1k' in text
            if width==1440:await page.screenshot(path=str(ROOT/'data/verification/record-details.png'))
            await page.locator('#closeJobDetails').click()
            assert await page.locator('#preview').evaluate('(x)=>x.open')
            await page.locator('#closePreview').click()
            await page.locator('[data-mode=portrait]').click()
            await page.locator('#draw').click()
            items=await page.evaluate('makeItems()')
            assert all(items[0].get(kind+'_id') for kind in ('outfit','pose','face'))
            # Display an expired fixture without changing the real database.
            await page.evaluate("jobsCache=[{id:'e'.repeat(32),state:'expired',config:{prompt:'保留参数'},image_extension:'.png'}];renderJobs()")
            assert await page.locator('#jobs [data-preview]').count()==0
            assert '图片超过 30 天' in await page.locator('#jobs').inner_text()
            assert await page.locator('#jobs [data-details]').count()==1
            assert await page.locator('#openDownload').is_disabled()
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            assert not errors,errors
            print('Record ordering, removed favorites, preview details, old IDs, new IDs and expiry UI:',width,'passed',flush=True)
            await page.close()
        await browser.close()


if __name__=='__main__':asyncio.run(main())
