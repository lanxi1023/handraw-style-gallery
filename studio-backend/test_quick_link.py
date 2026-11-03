import hashlib
import json
from pathlib import Path
import secrets
import tempfile
from unittest.mock import patch
from fastapi.testclient import TestClient
import remote_gateway as gateway


def main():
    with tempfile.TemporaryDirectory() as tmp:
        data=Path(tmp)
        with patch.multiple(gateway,DATA=data,CONFIG=data/'config.json',AUTH=data/'auth.json',DB=data/'sessions.db'):
            gateway.CONFIG.write_text(json.dumps({'enabled':True,'api_origin':'https://api.lanxi1023.com','web_origins':['https://www.lanxi1023.com']}))
            users=[{'id':name,'username':name,'enabled':True} for name in ('01','02')]
            gateway.AUTH.write_text(json.dumps({'version':2,'users':users}))
            token=secrets.token_urlsafe(32);digest=hashlib.sha256(token.encode()).hexdigest()
            with gateway.database() as db:db.execute('INSERT INTO shortcuts VALUES (?,?,1,0,NULL)',(digest,'01'))
            headers={'Origin':'https://www.lanxi1023.com','X-Studio-Client':'accounts-v1'}
            with TestClient(gateway.app,base_url='https://api.lanxi1023.com') as client:
                assert client.get('/api/jobs',headers=headers).status_code==401
                assert client.get('/auth/shortcut?token='+token,headers=headers).status_code==404
                assert client.post('/auth/shortcut',headers={'Origin':'https://evil.test'},json={'token':token}).status_code==403
                assert client.post('/auth/shortcut',headers={'X-Studio-Client':'accounts-v1'},json={'token':token}).status_code==403
                assert client.post('/auth/shortcut',headers={'Origin':headers['Origin']},json={'token':token}).status_code==426
                assert client.post('/auth/shortcut',headers=headers,json={'token':'wrong'}).status_code==401
                result=client.post('/auth/shortcut',headers=headers,json={'token':token,'user_id':'02'})
                assert result.status_code==200 and result.json()['user']['id']=='01'
                assert 'HttpOnly' in result.headers['set-cookie'] and 'Secure' in result.headers['set-cookie']
                assert token not in result.text and token not in result.headers['set-cookie']
                assert client.get('/auth/session',headers=headers).json()['user']['id']=='01'
                assert client.post('/api/batches',headers=headers,json={}).status_code==403
                assert client.get('/api/admin/pool',headers=headers).status_code==404
                with gateway.database() as db:
                    row=db.execute('SELECT * FROM sessions').fetchone()
                    assert row['shortcut']==digest and token not in str(dict(row))
                    db.execute('UPDATE shortcuts SET enabled=0 WHERE digest=?',(digest,))
                assert not client.get('/auth/session',headers=headers).json()['authenticated']
                assert client.get('/api/jobs',headers=headers).status_code==401
                assert client.post('/auth/shortcut',headers=headers,json={'token':token}).status_code==401
                with gateway.database() as db:db.execute('UPDATE shortcuts SET enabled=1 WHERE digest=?',(digest,))
                users[0]['enabled']=False;gateway.AUTH.write_text(json.dumps({'version':2,'users':users}))
                assert client.post('/auth/shortcut',headers=headers,json={'token':token}).status_code==401
                users[0]['enabled']=True;gateway.AUTH.write_text(json.dumps({'version':2,'users':users}))
                result=client.post('/auth/shortcut',headers=headers,json={'token':token})
                assert result.status_code==200
                assert client.post('/auth/logout',headers={**headers,'X-Studio-CSRF':result.json()['csrf']}).status_code==200
                assert not client.get('/auth/session',headers=headers).json()['authenticated']
                assert client.post('/auth/shortcut',headers=headers,json={'token':token}).status_code==200
                with gateway.database() as db:db.execute('UPDATE shortcuts SET enabled=0 WHERE digest=?',(digest,))
                for _ in range(10):assert client.post('/auth/shortcut',headers=headers,json={'token':secrets.token_urlsafe(32)}).status_code==401
                assert client.post('/auth/shortcut',headers=headers,json={'token':token}).status_code==429
    print('Quick link: owner binding, cookie, CSRF, origin/client checks, revocation, account disable, reuse and throttling passed.')


if __name__=='__main__':main()
