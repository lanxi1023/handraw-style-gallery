"""Real thumbnail bytes and MIME types; no account or generation changes."""
import asyncio
import io
from pathlib import Path
import httpx
from PIL import Image
import server


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://127.0.0.1:8768') as client:
        styles=(await client.get('/api/styles')).json()['items']
        samples={}
        for item in styles:
            if item['image']:samples.setdefault(item['id'].split('-')[0],item['image'])
        for label,path in {**samples,'layout':'/styles/layouts/comic-storyboards/SB-001.webp','color':'/styles/colors/C-01.webp'}.items():
            response=await client.get(path)
            assert response.status_code==200,(label,response.status_code)
            assert response.headers['content-type']=='image/webp',(label,response.headers['content-type'])
            with Image.open(io.BytesIO(response.content)) as image:
                image.verify()
            print('Valid WebP thumbnail and media type:',label)


asyncio.run(main())
