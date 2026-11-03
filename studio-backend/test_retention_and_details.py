import asyncio
import io
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from contextlib import closing
from unittest.mock import AsyncMock,patch
from fastapi import HTTPException
from PIL import Image
import server
from retention import cleanup,RETENTION_SECONDS,remove_owned_file


async def main():
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder);data=root/'studio';pool=root/'pool';pool.mkdir();data.mkdir()
        with patch.multiple(server,DATA=data,DB=data/'studio.sqlite3',UPLOADS=data/'uploads'):
            server.initialize();(data/'images').mkdir()
            now=time.time();old=now-31*86400;recent=now-86400
            names={x:x*64+'.png' for x in 'abcdef0'}
            image=io.BytesIO();Image.new('RGB',(8,12),'blue').save(image,format='PNG');png=image.getvalue()
            for name in names.values():(pool/name).write_bytes(png)
            fixtures=[('a','01',old,'a','completed'),('b','01',recent,'b','completed'),('c','02',old,'c','completed'),('d','02',recent,'c','completed'),('e','01',old,'d','completed'),('f','01',now-RETENTION_SECONDS,'e','completed'),('0','01',old,'f','processing'),('1','01',now,None,'failed')]
            for letter,owner,stamp,name,state in fixtures:
                jid=letter*32
                config={'mode':'portrait','outfit':'/costume/SPR-0001.png','outfit_id':'SPR-0001','pose_id':'P001','face_id':'FACE-001','prompt':'base'}
                with server.conn() as db:
                    db.execute('INSERT INTO jobs(id,batch_id,created_at,updated_at,state,prompt,refs,ratio,style,label,config,image_url,finished_at,owner_id,favorite) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)',(jid,'a'*32,stamp,stamp,state,'Actual submitted prompt','[]','3:4','','test',json.dumps(config),'http://127.0.0.1:8000/api/pool/images/'+names[name] if name else None,stamp if state=='completed' else None,owner))
                (data/'images'/jid).write_bytes(png)
            upload=data/'uploads'/('9'*32+'.jpg');upload.write_bytes(b'private material must stay')
            pool_db=root/'pool.sqlite3'
            with closing(sqlite3.connect(pool_db)) as db:
                db.execute('CREATE TABLE tasks(status,completed_at,created_at,result_json,image_path)')
                db.execute('INSERT INTO tasks VALUES (?,?,?,?,?)',('completed',now,now,json.dumps({'image_url':names['d']}),None))
                db.execute('INSERT INTO tasks VALUES (?,?,?,?,?)',('completed',old,old,json.dumps({'image_url':names['0']}),None))
                db.commit()
            report=cleanup(server.DB,data,pool,pool_db,now=now)
            assert report['newly_expired']==4 and report['studio_files_removed']==4 and report['pool_files_removed']==3,report
            assert not (pool/names['0']).exists()
            assert not (pool/names['a']).exists() and not (pool/names['e']).exists()
            assert all((pool/names[x]).exists() for x in 'bcdf')
            assert upload.exists() and (data/'images'/('0'*32)).exists()
            assert (data/'images'/('b'*32)).exists() and (data/'images'/('d'*32)).exists()
            details=await server.job_details('b'*32)
            assert details['dimensions']=={'width':8,'height':12}
            assert details['config']['outfit_id']=='SPR-0001' and details['prompt']=='Actual submitted prompt'
            assert (await server.job_details('a'*32))['state']=='expired'
            try:await server.job_details('c'*32);raise AssertionError('Another account details leaked')
            except HTTPException as e:assert e.status_code==404
            with patch.object(server,'pool_request',AsyncMock()) as provider:
                for action in (lambda:server.image('e'*32),lambda:server.image_reference('e'*32),lambda:server.download_images(server.DownloadSelection(ids=['a'*32,'b'*32]))):
                    try:await action();raise AssertionError('Expired image was restored or downloaded')
                    except HTTPException as e:assert e.status_code==410
                provider.assert_not_called()
            all_jobs=(await server.jobs())['items']
            assert all_jobs[0]['id']=='b'*32 and all_jobs[-1]['state']=='failed'
            batch=(await server.jobs('a'*32))['items']
            completed=[x for x in batch if x['state'] in ('completed','expired')]
            assert completed[0]['finished_at']<=completed[-1]['finished_at']
            assert cleanup(server.DB,data,pool,pool_db,now=now)['studio_files_removed']==0
            outside=root/'outside';outside.write_text('keep')
            assert remove_owned_file(data/'images','../outside',re.compile(r'^[a-f0-9]{32}$'))==0
            assert outside.read_text()=='keep'
            print('30-day boundary, duplicate retention, upload preservation, no resurrection, details ownership and sorting passed.')


asyncio.run(main())
