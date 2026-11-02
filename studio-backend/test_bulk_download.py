import asyncio
import tempfile
import zipfile
from pathlib import Path

from fastapi import HTTPException
import server


async def check():
    with tempfile.TemporaryDirectory() as tmp:
        server.DATA = Path(tmp)
        server.UPLOADS = server.DATA / 'uploads'
        server.DB = server.DATA / 'studio.sqlite3'
        server.initialize()
        ids = ['a' * 32, 'b' * 32, 'c' * 32]
        (server.DATA / 'images').mkdir()
        for i, job_id in enumerate(ids):
            (server.DATA / 'images' / job_id).write_bytes(b'\x89PNG\r\n\x1a\n' + bytes([i]))
            with server.conn() as db:
                db.execute("INSERT INTO jobs(id,batch_id,created_at,updated_at,state,prompt,refs,ratio,style,label) VALUES(?,?,0,0,?,'','[]','1:1','','')", (job_id, 'batch', 'failed' if i == 2 else 'completed'))
        response = await server.download_images(server.DownloadSelection(ids=[ids[1], ids[0]]))
        with zipfile.ZipFile(response.path) as archive:
            assert archive.namelist() == ['作品-001.png', '作品-002.png']
            assert archive.read('作品-001.png').endswith(b'\x01')
            assert archive.read('作品-002.png').endswith(b'\x00')
        await response.background()
        assert not Path(response.path).exists()
        assert all((server.DATA / 'images' / x).exists() for x in ids)
        for selection, expected in [([ids[0], ids[0]], 400), ([ids[2]], 409), (['d' * 32], 404)]:
            try:
                await server.download_images(server.DownloadSelection(ids=selection))
                raise AssertionError('expected rejection')
            except HTTPException as exc:
                assert exc.status_code == expected
    print('Bulk download selection, archive, cleanup and error checks passed')


asyncio.run(check())
