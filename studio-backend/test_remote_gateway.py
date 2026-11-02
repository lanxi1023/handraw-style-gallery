import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
import remote_gateway as gateway


def main():
    with tempfile.TemporaryDirectory() as tmp:
        gateway.DATA = Path(tmp)
        gateway.CONFIG = gateway.DATA / 'config.json'
        gateway.AUTH = gateway.DATA / 'auth.json'
        gateway.DB = gateway.DATA / 'sessions.db'
        gateway.CONFIG.write_text(json.dumps({'enabled': True, 'api_origin': 'https://api.lanxi1023.com', 'web_origins': ['https://www.lanxi1023.com']}))
        salt = bytes.fromhex('00' * 16)
        gateway.AUTH.write_text(json.dumps({'username': 'studio', 'salt': salt.hex(), 'hash': hashlib.scrypt(b'test-only-pass', salt=salt, n=16384, r=8, p=1).hex()}))
        original = httpx.AsyncClient
        calls = []
        async def upstream(request):
            calls.append(request)
            return httpx.Response(200, json={'items': [], 'ready': True})
        def factory(*args, **kwargs):
            kwargs['transport'] = httpx.MockTransport(upstream)
            return original(*args, **kwargs)
        with patch.object(gateway.httpx, 'AsyncClient', factory), TestClient(gateway.app, base_url='https://api.lanxi1023.com') as client:
            origin = {'Origin': 'https://www.lanxi1023.com'}
            assert client.get('/api/jobs', headers=origin).status_code == 401
            assert client.get('/uploads/' + 'a' * 32 + '.jpg', headers=origin).status_code == 401
            assert client.get('/auth/session', headers={'Origin': 'https://evil.test'}).status_code == 403
            pre = client.options('/api/jobs', headers={**origin, 'Access-Control-Request-Method': 'GET'})
            assert pre.status_code == 204 and pre.headers['access-control-allow-origin'] == origin['Origin']
            r = client.post('/auth/login', headers=origin, json={'username': 'studio', 'password': 'test-only-pass'})
            assert r.status_code == 200, r.text
            cookie = r.headers['set-cookie']
            assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=lax' in cookie
            csrf = r.json()['csrf']
            assert client.get('/api/jobs', headers=origin).status_code == 200
            assert client.get('/open-pool', headers=origin).status_code == 404
            assert client.get('/api/admin/pool', headers=origin).status_code == 404
            assert client.get('/catalog/private.json', headers=origin).status_code == 404
            assert client.post('/api/batches', headers=origin, json={}).status_code == 403
            assert client.post('/api/batches', headers={**origin, 'X-Studio-CSRF': csrf}, json={}).status_code == 200
            assert calls[-1].headers['origin'] == gateway.UPSTREAM
            assert 'cookie' not in calls[-1].headers
            assert client.post('/auth/logout', headers={**origin, 'X-Studio-CSRF': csrf}).status_code == 200
            assert client.get('/api/jobs', headers=origin).status_code == 401
            for _ in range(4):
                assert client.post('/auth/login', headers=origin, json={'password': 'wrong'}).status_code == 401
            assert client.post('/auth/login', headers=origin, json={'password': 'wrong'}).status_code == 429
            gateway.CONFIG.write_text('{"enabled":false}')
            assert client.get('/api/health').status_code == 503
    print('Remote authentication, cookies, CSRF, CORS, route isolation, revocation and throttling passed.')


if __name__ == '__main__':
    main()
