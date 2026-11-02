"""Authenticated, narrowly scoped bridge to the loopback-only studio.

This listener must be published through an HTTPS tunnel, never the pool port.
No account credentials or pool administration routes are exposed here.
"""
import asyncio
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from bridge_identity import assertion

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
CONFIG = DATA / 'remote-config.json'
AUTH = DATA / 'remote-auth.json'
DB = DATA / 'remote.sqlite3'
COOKIE = '__Host-studio_session'
SESSION_SECONDS = 7 * 24 * 60 * 60
UPSTREAM = 'http://127.0.0.1:8768'
MAX_BODY = 13 * 1024 * 1024
CLIENT_VERSION = 'accounts-v1'


def auth_records():
    data = json.loads(AUTH.read_text(encoding='utf-8'))
    return data, data.get('users', [{**data, 'id': '01', 'enabled': True}])


@contextmanager
def database():
    DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL)')
            if 'user_id' not in {r['name'] for r in db.execute('PRAGMA table_info(sessions)')}:
                db.execute("ALTER TABLE sessions ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")
            db.execute('CREATE TABLE IF NOT EXISTS attempts(ip TEXT NOT NULL, at REAL NOT NULL)')
            if 'account' not in {r['name'] for r in db.execute('PRAGMA table_info(attempts)')}:
                db.execute("ALTER TABLE attempts ADD COLUMN account TEXT NOT NULL DEFAULT ''")
            yield db
    finally:
        db.close()


def settings():
    try:
        config = json.loads(CONFIG.read_text(encoding='utf-8'))
        origin = config['api_origin']
        allowed = config['web_origins']
        if not re.fullmatch(r'https://[a-z0-9.-]+', origin):
            return None
        if not allowed or any(not re.fullmatch(r'https://[a-z0-9.-]+', x) for x in allowed):
            return None
        return config if config.get('enabled') and AUTH.is_file() else None
    except (OSError, ValueError, KeyError):
        return None


def session(request):
    token = request.cookies.get(COOKIE, '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    with database() as db:
        row = db.execute('SELECT * FROM sessions WHERE token=? AND expires>?', (digest, time.time())).fetchone()
    if not row:
        return None
    _, users = auth_records()
    user = next((x for x in users if x['id'] == row['user_id'] and x.get('enabled', True)), None)
    return {**dict(row), 'username': user['username']} if user else None


def allowed_route(method, path):
    if method == 'GET':
        return bool(re.fullmatch(r'/api/(styles|handdraw-options|assets|batches|jobs)', path)
                    or re.fullmatch(r'/api/jobs/[a-f0-9]{32}/image', path)
                    or path in ('/catalog/data.js', '/catalog/extra-data.js')
                    or re.fullmatch(r'/(costume|poses|styles)/[^\\\x00]+\.(png|jpg|jpeg|webp)', path, re.I)
                    or re.fullmatch(r'/uploads/[a-f0-9]{32}\.jpg', path))
    if method == 'POST':
        return bool(path in ('/api/upload', '/api/batches', '/api/compile', '/api/assets', '/api/jobs/download')
                    or re.fullmatch(r'/api/jobs/[a-f0-9]{32}/(retry|favorite|reference)', path))
    return method == 'DELETE' and bool(re.fullmatch(r'/api/assets/[a-f0-9]{32}', path))


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware('http')
async def protect(request: Request, call_next):
    config = settings()
    if not config:
        return JSONResponse({'detail': '远程连接尚未启用'}, status_code=503)
    if request.headers.get('host', '').lower() != config['api_origin'][8:]:
        return Response(status_code=403)
    origin = request.headers.get('origin')
    origins = {*config['web_origins'], config['api_origin']}
    if origin and origin not in origins:
        return Response(status_code=403)
    if request.method == 'OPTIONS':
        if not origin or request.headers.get('access-control-request-method') not in ('GET', 'POST', 'DELETE'):
            return Response(status_code=403)
        response = Response(status_code=204)
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, DELETE'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Studio-CSRF, X-Studio-Client, X-Studio-User'
        response.headers['Access-Control-Max-Age'] = '600'
    else:
        public = request.url.path in ('/api/health', '/auth/session', '/auth/login')
        request.state.session = session(request)
        data, _ = auth_records()
        if (data.get('version') == 2 and request.url.path in ('/auth/session', '/auth/login')
                and request.headers.get('x-studio-client') != CLIENT_VERSION):
            response = JSONResponse({'detail': '工作台已更新，请刷新网页后登录', 'code': 'update_required'}, status_code=426)
        elif not public and not request.state.session:
            response = JSONResponse({'detail': '请先登录工作台'}, status_code=401)
        elif (not public and request.url.path.startswith('/api/') and request.headers.get('x-studio-user')
              and request.headers['x-studio-user'] != request.state.session['user_id']):
            response = JSONResponse({'detail': '登录账号已切换，正在刷新工作台', 'code': 'account_changed'}, status_code=409)
        elif request.method not in ('GET', 'HEAD') and (not origin or
                (not public and not hmac.compare_digest(request.headers.get('x-studio-csrf', ''), request.state.session['csrf']))):
            response = JSONResponse({'detail': '请刷新页面后重试'}, status_code=403)
        else:
            response = await call_next(request)
    if origin:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Vary'] = 'Origin'
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.get('/api/health')
async def health():
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            r = await client.get(UPSTREAM + '/api/health')
            ready = r.is_success and r.json().get('ready') is True
    except Exception:
        ready = False
    return {'service': 'studio-remote', 'ready': ready}


@app.get('/auth/session')
async def auth_session(request: Request):
    s = request.state.session
    return {'authenticated': bool(s), 'csrf': s['csrf'] if s else None,
            'user': {'id': s['user_id'], 'username': s['username']} if s else None}


@app.post('/auth/login')
async def login(request: Request):
    ip = request.headers.get('cf-connecting-ip') or (request.client.host if request.client else 'unknown')
    now = time.time()
    try:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 2048:
                raise ValueError()
        body = json.loads(raw)
        username, password = body.get('username', ''), body.get('password', '')
        if not isinstance(username, str) or len(username) > 64 or not isinstance(password, str) or len(password) > 256:
            raise ValueError()
    except (ValueError, AttributeError):
        return JSONResponse({'detail': '用户名或密码不正确'}, status_code=401)
    with database() as db:
        db.execute('DELETE FROM attempts WHERE at<?', (now - 300,))
        count = db.execute('SELECT COUNT(*) FROM attempts WHERE ip=?', (ip,)).fetchone()[0]
        total = db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
        account_count = db.execute('SELECT COUNT(*) FROM attempts WHERE account=?', (username,)).fetchone()[0]
        if count >= 5 or total >= 30 or account_count >= 20:
            return JSONResponse({'detail': '尝试次数较多，请五分钟后再试'}, status_code=429)
        db.execute('INSERT INTO attempts(ip,at,account) VALUES (?,?,?)', (ip, now, username))
    try:
        _, users = auth_records()
        account = next((x for x in users if hmac.compare_digest(username, x['username']) and x.get('enabled', True)), None)
        auth = account or users[0]
        digest = await asyncio.to_thread(hashlib.scrypt, password.encode(), salt=bytes.fromhex(auth['salt']), n=16384, r=8, p=1)
        valid = hmac.compare_digest(digest.hex(), auth['hash']) and account is not None
    except Exception:
        valid = False
    if not valid:
        return JSONResponse({'detail': '用户名或密码不正确'}, status_code=401)
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with database() as db:
        db.execute('DELETE FROM attempts WHERE ip=? AND account=?', (ip, username))
        db.execute('DELETE FROM sessions WHERE expires<?', (now,))
        db.execute('INSERT INTO sessions(token,csrf,expires,user_id) VALUES (?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), csrf, now + SESSION_SECONDS, account['id']))
    response = JSONResponse({'authenticated': True, 'csrf': csrf, 'user': {'id': account['id'], 'username': account['username']}})
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, secure=True, httponly=True, samesite='lax', path='/')
    return response


@app.post('/auth/logout')
async def logout(request: Request):
    with database() as db:
        db.execute('DELETE FROM sessions WHERE token=?', (request.state.session['token'],))
    response = JSONResponse({'ok': True})
    response.delete_cookie(COOKIE, secure=True, httponly=True, samesite='lax', path='/')
    return response


@app.api_route('/{path:path}', methods=['GET', 'POST', 'DELETE'])
async def proxy(request: Request, path: str):
    path = '/' + path
    if '..' in path.split('/') or not allowed_route(request.method, path):
        return Response(status_code=404)
    if request.headers.get('content-length', '').isdigit() and int(request.headers['content-length']) > MAX_BODY:
        return JSONResponse({'detail': '上传内容过大'}, status_code=413)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY:
            return JSONResponse({'detail': '上传内容过大'}, status_code=413)
    headers = {'Origin': UPSTREAM}
    try:
        headers.update(assertion(request.state.session['user_id'], request.method, path, DATA / 'bridge-secret.txt'))
    except (OSError, ValueError):
        return JSONResponse({'detail': '账号隔离配置尚未就绪，请稍后重试'}, status_code=503)
    if request.headers.get('content-type'):
        headers['Content-Type'] = request.headers['content-type']
    client = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=5), trust_env=False)
    try:
        upstream = await client.send(client.build_request(request.method, UPSTREAM + path,
                                    params=request.query_params, headers=headers, content=bytes(body)), stream=True)
    except httpx.RequestError:
        await client.aclose()
        return JSONResponse({'detail': '生图电脑暂时无法连接，请稍后重试'}, status_code=503)

    async def close():
        await upstream.aclose()
        await client.aclose()

    copied = {k: v for k, v in upstream.headers.items() if k in ('content-type', 'content-disposition')}
    return StreamingResponse(upstream.aiter_bytes(), status_code=upstream.status_code,
                             headers=copied, background=BackgroundTask(close))
