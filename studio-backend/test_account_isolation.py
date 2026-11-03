import asyncio
import hashlib
import io
import json
import sqlite3
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from PIL import Image
from starlette.staticfiles import StaticFiles

import remote_gateway as gateway
import server
from bridge_identity import assertion


def main():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        with patch.multiple(server, DATA=data, UPLOADS=data/'uploads', DB=data/'studio.sqlite3'), \
             patch.multiple(gateway, DATA=data, CONFIG=data/'remote-config.json', AUTH=data/'auth.json', DB=data/'remote.sqlite3'):
            # Build the old schema and verify records migrate to 01 exactly once.
            with server.conn() as db:
                db.execute('CREATE TABLE assets(id TEXT PRIMARY KEY,url TEXT,name TEXT,category TEXT,created_at REAL)')
                db.execute("INSERT INTO assets VALUES ('legacy','/uploads/legacy.jpg','old','人物',0)")
            server.initialize()
            assert server.rows('SELECT owner_id FROM assets')[0]['owner_id'] == '01'
            server.initialize()
            assert len(server.rows('SELECT * FROM assets')) == 1
            data.joinpath('bridge-secret.txt').write_text('11'*32, encoding='ascii')
            users = []
            for name in ('01','02','03','04'):
                salt = bytes.fromhex(name*16)
                users.append({'id':name,'username':name,'salt':salt.hex(),'hash':hashlib.scrypt(('test-'+name).encode(),salt=salt,n=16384,r=8,p=1).hex()})
            gateway.AUTH.write_text(json.dumps({'version':2,'users':users}))
            gateway.CONFIG.write_text(json.dumps({'enabled':True,'api_origin':'https://api.lanxi1023.com','web_origins':['https://www.lanxi1023.com']}))
            mount = next(x for x in server.app.routes if getattr(x,'path',None)=='/uploads')
            old_mount = mount.app
            mount.app = StaticFiles(directory=server.UPLOADS)
            original = httpx.AsyncClient
            def local_proxy(*args, **kwargs):
                kwargs['transport'] = httpx.ASGITransport(app=server.app)
                return original(*args, **kwargs)
            clients = {}
            try:
                with patch.object(gateway.httpx,'AsyncClient',local_proxy):
                    for name in ('01','02','03','04'):
                        client=TestClient(gateway.app,base_url='https://api.lanxi1023.com')
                        client.headers.update({'Origin':'https://www.lanxi1023.com','X-Studio-Client':'accounts-v1'})
                        assert client.post('/auth/login',headers={'X-Studio-Client':''},json={'username':name,'password':'test-'+name}).status_code==426
                        r=client.post('/auth/login',json={'username':name,'password':'test-'+name})
                        assert r.status_code==200,r.text
                        assert r.json()['user']['id']==name
                        client.headers.update({'X-Studio-CSRF':r.json()['csrf'],'X-Studio-User':name})
                        clients[name]=client
                    a,b=clients['01'],clients['02']
                    payload={'items':[{'mode':'general','prompt':'private prompt','references':[]}],'owner_id':'02'}
                    ba=a.post('/api/batches',json=payload).json()['batch_id']
                    bb=b.post('/api/batches',json=payload).json()['batch_id']
                    ja=a.get('/api/jobs').json()['items'][0]['id']
                    jb=b.get('/api/jobs').json()['items'][0]['id']
                    assert a.get('/api/jobs',params={'batch_id':bb,'owner_id':'02'}).json()['items']==[]
                    assert b.get('/api/jobs',params={'batch_id':ba}).json()['items']==[]
                    assert a.get('/api/batches').json()['items'][0]['batch_id']==ba
                    assert b.get('/api/batches').json()['items'][0]['batch_id']==bb
                    assert clients['03'].get('/api/jobs').json()['items']==[]
                    assert clients['04'].get('/api/jobs').json()['items']==[]
                    buffer=io.BytesIO();Image.new('RGB',(8,8),'blue').save(buffer,format='PNG')
                    (data/'images').mkdir()
                    for jid in (ja,jb):
                        (data/'images'/jid).write_bytes(buffer.getvalue())
                        server.update(jid,state='completed',finished_at=time.time())
                    assert a.get('/api/jobs/'+ja+'/image').status_code==200
                    assert b.get('/api/jobs/'+ja+'/image?internal=true').status_code==404
                    assert a.get('/api/jobs/'+ja+'/details').status_code==200
                    assert b.get('/api/jobs/'+ja+'/details').status_code==404
                    assert b.post('/api/jobs/'+ja+'/favorite',json={'favorite':True}).status_code==404
                    assert b.post('/api/jobs/'+ja+'/retry').status_code==404
                    assert b.post('/api/jobs/'+ja+'/reference').status_code==404
                    assert b.post('/api/jobs/download',json={'ids':[ja,jb]}).status_code==404
                    assert b.post('/api/jobs/download',json={'ids':[jb]}).content.startswith(b'PK')
                    assert b.get('/api/jobs',headers={'X-Studio-User':'01'}).status_code==409
                    assert b.get('/api/jobs',headers={'X-Studio-Owner':'01','X-Studio-Proof':'forged'}).json()['items'][0]['id']==jb
                    ra=a.post('/api/upload',files={'file':('a.png',buffer.getvalue(),'image/png')}).json()['url']
                    rb=b.post('/api/upload',files={'file':('b.png',buffer.getvalue(),'image/png')}).json()['url']
                    assert a.get(ra).status_code==200 and b.get(rb).status_code==200
                    assert b.get(ra).status_code==404 and a.get(rb).status_code==404
                    assert b.post('/api/assets',json={'url':ra,'name':'stolen','category':'人物'}).status_code==404
                    assert a.post('/api/assets',json={'url':ra,'name':'private','category':'人物'}).status_code==200
                    asset=a.get('/api/assets').json()['items'][0]
                    assert b.get('/api/assets').json()['items']==[]
                    assert b.delete('/api/assets/'+asset['id']).status_code==404
                    item={'mode':'general','prompt':'test','references':[{'url':ra,'purpose':'reference'}]}
                    assert b.post('/api/compile',json=item).status_code==404
                    assert b.post('/api/batches',json={'items':[item]}).status_code==404
                    assert a.post('/api/compile',json=item).status_code==200
                    reused=b.post('/api/jobs/'+jb+'/reference').json()['url']
                    assert b.get(reused).status_code==200 and a.get(reused).status_code==404
                    assert clients['03'].get('/catalog/data.js').status_code==200
                    # Context must stay separate during overlapping requests.
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        checks=list(pool.map(lambda n: clients[n].get('/api/jobs').json()['items'],['01','02','03','04']*3))
                    assert all([x['id'] for x in group]==([ja] if i%4==0 else [jb] if i%4==1 else []) for i,group in enumerate(checks))
                    assert asyncio.run(server.cached_image(jb)).status_code==200
                    with TestClient(server.app,base_url='http://127.0.0.1:8768') as local:
                        assert local.get(rb).status_code==200  # pool's loopback reference fetch
                        assert local.get('/api/jobs',headers={'X-Studio-Owner':'02','X-Studio-Time':'1','X-Studio-Proof':'forged'}).status_code==403
                    print('Four-account isolation: lists, files, references, ZIP, edits, assets, spoofing, old clients and concurrent requests passed.')
            finally:
                mount.app=old_mount
                for client in clients.values():client.close()


if __name__=='__main__':main()
