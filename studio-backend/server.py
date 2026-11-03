"""Local-first creative studio, backed by the existing personal image pool."""
import asyncio, json, mimetypes, os, re, sqlite3, tempfile, time, uuid, zipfile
from contextlib import asynccontextmanager,contextmanager
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlparse,unquote
import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, Response,RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from bridge_identity import verified_owner
from retention import RETENTION_DAYS,expires_at,is_expired,cleanup as cleanup_images

# Windows installations may not register WebP. FileResponse otherwise falls back
# to text/plain, which browsers block for authenticated cross-origin images.
for extension, content_type in {'.webp':'image/webp','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg'}.items():
    mimetypes.add_type(content_type, extension)

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'data'; UPLOADS=DATA/'uploads'; DB=DATA/'studio.sqlite3'
POOL=Path(r'E:\号池'); SECRET=POOL/'data'/'local-secrets.json'
CATALOG=Path.home()/'Documents/Codex/2026-10-29/new-chat/outputs/灵感图鉴'
COSTUMES=CATALOG.parent/'costume-gallery-full'
SKILL=Path.home()/'.codex/skills/handraw-style'
STYLE_SKILL=SKILL/'skills/handdraw-style-prompter'
STYLES_FILE=STYLE_SKILL/'references/styles.json'
STYLE_SCRIPTS=STYLE_SKILL/'scripts'
import sys
sys.path.insert(0,str(STYLE_SCRIPTS))
from resolve_reference import resolve
from layout_library import detect_language,resolve_layout
from prompt_style import GRAPHIC_TEXT_SUFFIX,REFERENCE_ISOLATION_ZH,REFERENCE_ISOLATION_EN,REDRAW_INSTRUCTION_ZH,REDRAW_INSTRUCTION_EN

POOL_URL='http://127.0.0.1:8000'
ORIGIN='http://127.0.0.1:8768'
app_jobs=set()
account_context=ContextVar('studio_account',default='01')

def owner():return account_context.get()

def require_upload(name):
    if not re.fullmatch(r'[a-f0-9]{32}\.jpg',name):raise HTTPException(404,'素材不存在')
    found=rows('SELECT name FROM uploads WHERE name=? AND owner_id=?',(name,owner()))
    if not found:raise HTTPException(404,'素材不存在')

def register_upload(name):
    with conn() as c:c.execute('INSERT INTO uploads(name,owner_id,created_at) VALUES (?,?,?)',(name,owner(),time.time()))

@contextmanager
def conn():
    DATA.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(DB,timeout=15);c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=15000')
    try:
        with c:yield c
    finally:c.close()

def initialize():
    UPLOADS.mkdir(parents=True,exist_ok=True)
    with conn() as c:
        c.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY,batch_id TEXT NOT NULL, created_at REAL NOT NULL,updated_at REAL NOT NULL, state TEXT NOT NULL, prompt TEXT NOT NULL, refs TEXT NOT NULL, ratio TEXT NOT NULL, style TEXT NOT NULL, label TEXT NOT NULL, pool_id TEXT, image_url TEXT, error TEXT, finished_at REAL)')
        if 'config' not in {r['name'] for r in c.execute('PRAGMA table_info(jobs)')}:
            c.execute("ALTER TABLE jobs ADD COLUMN config TEXT NOT NULL DEFAULT '{}'")
        if 'favorite' not in {r['name'] for r in c.execute('PRAGMA table_info(jobs)')}:
            c.execute('ALTER TABLE jobs ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0')
        if 'expired_at' not in {r['name'] for r in c.execute('PRAGMA table_info(jobs)')}:
            c.execute('ALTER TABLE jobs ADD COLUMN expired_at REAL')
        c.execute('CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, url TEXT NOT NULL, name TEXT NOT NULL, category TEXT NOT NULL, created_at REAL NOT NULL)')
        for table in ('jobs','assets'):
            if 'owner_id' not in {r['name'] for r in c.execute('PRAGMA table_info('+table+')')}:
                c.execute("ALTER TABLE "+table+" ADD COLUMN owner_id TEXT NOT NULL DEFAULT '01'")
        migrate_uploads=not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='uploads'").fetchone()
        c.execute('CREATE TABLE IF NOT EXISTS uploads (name TEXT PRIMARY KEY,owner_id TEXT NOT NULL,created_at REAL NOT NULL)')
        for path in UPLOADS.glob('*.jpg') if migrate_uploads else ():
            if re.fullmatch(r'[a-f0-9]{32}\.jpg',path.name):
                c.execute("INSERT OR IGNORE INTO uploads VALUES (?,'01',?)",(path.name,path.stat().st_mtime))
        c.execute('CREATE INDEX IF NOT EXISTS jobs_owner_created ON jobs(owner_id,created_at)')
        c.execute('CREATE INDEX IF NOT EXISTS assets_owner_created ON assets(owner_id,created_at)')
        # A lost submission response cannot safely be repeated without confirmation.
        c.execute("UPDATE jobs SET state='uncertain',error='提交结果未确认，请查看任务记录后再重试。' WHERE state='submitting' AND pool_id IS NULL")

def rows(sql,args=()):
    with conn() as c:return [dict(x) for x in c.execute(sql,args).fetchall()]

def update(job_id,**fields):
    fields['updated_at']=time.time()
    keys=list(fields)
    with conn() as c:c.execute('UPDATE jobs SET '+','.join(k+'=?' for k in keys)+' WHERE id=?',[fields[k] for k in keys]+[job_id])

def pool_key():
    try:return json.loads(SECRET.read_text(encoding='utf-8-sig'))['api_key']
    except Exception:raise RuntimeError('无法连接号池，请先启动号池工作台。')

async def pool_request(client,method,path,**kwargs):
    headers=dict(kwargs.pop('headers',{}));headers['Authorization']='Bearer '+pool_key()
    response=await client.request(method,POOL_URL+path,headers=headers,**kwargs)
    if not response.is_success:
        try:detail=response.json().get('detail') or '号池请求失败'
        except Exception:detail='号池请求失败'
        raise RuntimeError(str(detail)[:200])
    return response

def safe_ref(value):
    value=str(value or '').strip()
    if not value:return None
    if value.startswith('/costume/') or value.startswith('/poses/') or value.startswith('/uploads/') or value.startswith('/styles/'):
        path=unquote(urlparse(value).path)
        if '..' in path.split('/') or '\\' in path:raise ValueError('参考图路径无效')
        if path.startswith('/uploads/'):
            require_upload(path[len('/uploads/'):])
        return ORIGIN+value
    parsed=urlparse(value)
    if parsed.scheme=='https' and parsed.hostname in {'ai-portrait-gallery.pages.dev','ai-pose-depth-gallery.pages.dev'}:
        return value
    raise ValueError('参考图来源不受支持')

def style_info(style_id):
    if not style_id:return None
    data=json.loads(STYLES_FILE.read_text(encoding='utf-8'))
    item=next((x for x in data if x.get('number')==style_id),None)
    if not item:raise ValueError('画风不存在')
    decision=resolve('gpt-image-2',style_id)
    return item,decision

def compile_item(item):
    if item.mode not in {'general','portrait','handdraw'}: raise ValueError('创作方式无效')
    if item.mode != 'portrait' and (item.outfit or item.pose or item.face): raise ValueError('人物素材只能用于人物写真')
    if item.mode != 'handdraw' and (item.layout or item.color): raise ValueError('图型和配色只能用于手绘创作')
    if item.style and item.mode not in {'handdraw','portrait'}: raise ValueError('画风只能用于手绘创作或人物写真')
    parts=['用户要求：'+item.prompt.strip()]
    refs=[]
    for kind,url in [('衣装',item.outfit),('姿势',item.pose),('脸部',item.face)]:
        if url:
            refs.append(safe_ref(url));parts.append(f'第 {len(refs)} 张参考图只用于参考{kind}。')
    for reference in item.references:
        ref=safe_ref(reference.url)
        if not ref: raise ValueError('参考图地址不能为空')
        refs.append(ref); parts.append(f'第 {len(refs)} 张参考图的用途：{reference.purpose or "参考画面"}。')
    layout=None
    language=detect_language(item.prompt)
    if item.mode == 'handdraw':
        if item.layout:
            layout=resolve_layout(item.layout)
            if layout['id']=='SC-021':
                raise ValueError('该图型需要先确定专门的构图方案，工作台暂不支持；请选择其他图型。')
            parts.append(('图型：'+str(layout['name'])) if language=='zh' else ('Layout: '+str(layout.get('name_en') or layout['name'])))
            parts.append(('排版要求：' if language=='zh' else 'Layout instructions: ')+str(layout['prompts'][language]))
        if item.color:
            records=json.loads((STYLE_SKILL/'references'/'colors.json').read_text(encoding='utf-8'))
            record=next((x for x in records if x.get('id')==item.color),None)
            if not record:raise ValueError('配色不存在')
            parts.append(('配色：'+str(record.get('name_zh') or record.get('name') or item.color)) if language=='zh' else ('Theme color: '+str(record.get('name_en') or record.get('name') or item.color)))
            parts.append(str(record.get('prompt_zh' if language=='zh' else 'prompt_en') or ''))
    info=style_info(item.style)
    if info:
        record,decision=info
        parts.append('最终画面的视觉风格以所选画风为准。' if language=='zh' else 'Use the selected style for the final visual treatment.')
        parts.append(('风格名称：' if language=='zh' else 'Style name: ')+str(record.get('generation_name') or ''))
        parts.append(('参考作者与风格名称：' if language=='zh' else 'Reference author and style name: ')+str(record.get('reference') or ''))
        if decision.get('include_prompt_traits') and decision.get('prompt_traits'):
            parts.append(('核心风格特征：' if language=='zh' else 'Core style traits: ')+decision['prompt_traits'])
        if decision.get('use_reference_image'):
            path=decision.get('reference_path')
            if not path:raise ValueError('所选画风需要参考图，但本机素材不存在。')
            relative=Path(path).resolve().relative_to((SKILL/'images').resolve()).as_posix()
            refs.append(ORIGIN+'/styles/'+relative)
            parts.append((f'第 {len(refs)} 张图片仅作画风参考。' if language=='zh' else f'Image {len(refs)} is a style reference only. ')+(REFERENCE_ISOLATION_ZH if language=='zh' else REFERENCE_ISOLATION_EN))
    if item.mode=='handdraw' and item.references and any(term in (item.prompt+' '+ ' '.join(x.purpose for x in item.references)).lower() for term in ('转手绘','改图','改风格','转风格','重绘','redraw','style transfer')):
        parts.append(REDRAW_INSTRUCTION_ZH if language=='zh' else REDRAW_INSTRUCTION_EN)
    if item.mode=='portrait' and item.style and (item.outfit or item.pose or item.face):
        parts.append(REDRAW_INSTRUCTION_ZH if language=='zh' else REDRAW_INSTRUCTION_EN)
    if layout and layout['category']!='ecommerce':
        parts.append(GRAPHIC_TEXT_SUFFIX)
    return '\n'.join(parts),refs

class Reference(BaseModel):
    url:str
    purpose:str=Field(default='',max_length=300)

class Item(BaseModel):
    mode:str='portrait'
    references:list[Reference]=Field(default_factory=list)
    ratio:str|None=None
    layout:str=''
    color:str=''
    compiled_prompt:str|None=Field(default=None,max_length=20000)
    label:str=Field(default='',max_length=120)
    prompt:str=Field(min_length=1,max_length=5000)
    outfit:str|None=None
    pose:str|None=None
    face:str|None=None
    style:str|None=None
    outfit_id:str|None=Field(default=None,max_length=120)
    pose_id:str|None=Field(default=None,max_length=120)
    face_id:str|None=Field(default=None,max_length=120)

class Batch(BaseModel):
    items:list[Item]=Field(min_length=1,max_length=50)
    ratio:str='1:1'

def public_job(row):
    out={k:row[k] for k in ('id','batch_id','created_at','updated_at','state','label','style','ratio','error','finished_at','favorite')}
    suffix=Path(urlparse(row['image_url'] or '').path).suffix.lower()
    out['image_extension']=suffix if suffix in ('.png','.jpg','.webp') else '.png'
    try:out['config']=json.loads(row['config'])
    except (ValueError,TypeError):out['config']={}
    out['image_expires_at']=expires_at(row) if row['state']=='completed' else None
    if is_expired(row):out['state']='expired'
    return out

async def retention_loop():
    while True:
        try:
            await asyncio.to_thread(cleanup_images,DB,DATA,POOL/'runtime/fpb-app/data/pool-images',POOL/'runtime/fpb-app/data/fpbrowser.db')
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Generated image cleanup failed')
        await asyncio.sleep(3600)

@asynccontextmanager
async def lifespan(app):
    initialize()
    worker=asyncio.create_task(dispatcher())
    maintenance=asyncio.create_task(retention_loop())
    try:yield
    finally:
        worker.cancel()
        maintenance.cancel()
        for task in list(app_jobs):task.cancel()
        await asyncio.gather(worker,maintenance,*list(app_jobs),return_exceptions=True)

app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None)

@app.middleware('http')
async def local_only(request:Request,call_next):
    host=request.headers.get('host','').lower();origin=request.headers.get('origin')
    if host not in ('127.0.0.1:8768','localhost:8768') or (origin and origin not in ('http://127.0.0.1:8768','http://localhost:8768')):
        return Response('Local access only',status_code=403)
    principal='01'
    scoped=any(request.headers.get(x) for x in ('x-studio-owner','x-studio-time','x-studio-proof'))
    if scoped:
        try:principal=verified_owner(request,DATA/'bridge-secret.txt')
        except (OSError,ValueError):return Response(status_code=403)
    token=account_context.set(principal)
    try:
        # The local pool can fetch submitted references directly over loopback.
        # Every gateway request carries a verified account and must pass ownership.
        if scoped and request.url.path.startswith('/uploads/'):
            try:require_upload(request.url.path[len('/uploads/'):])
            except HTTPException:return Response(status_code=404)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Cache-Control']='private, no-store'
        return response
    finally:account_context.reset(token)

app.mount('/static',StaticFiles(directory=str(ROOT/'static')),name='static')
app.mount('/catalog',StaticFiles(directory=str(CATALOG)),name='catalog')
app.mount('/costume',StaticFiles(directory=str(COSTUMES)),name='costume')
app.mount('/poses',StaticFiles(directory=str(CATALOG/'pose-assets')),name='poses')
app.mount('/styles',StaticFiles(directory=str(SKILL/'images')),name='styles')
app.mount('/uploads',StaticFiles(directory=str(UPLOADS),check_dir=False),name='uploads')

@app.get('/')
async def index():return FileResponse(ROOT/'static/index.html',headers={'Cache-Control':'no-store'})

@app.get('/api/health')
async def health():return {'service':'inspiration-studio','ready':True}

@app.get('/open-pool')
async def open_pool():
    try:
        secret=json.loads(SECRET.read_text(encoding='utf-8-sig'))
        async with httpx.AsyncClient(base_url=POOL_URL,timeout=10,trust_env=False) as client:
            r=await client.post('/api/admin/login',json={'username':secret['admin_username'],'password':secret['admin_password']})
            r.raise_for_status();token=r.json()['token']
        from urllib.parse import quote
        return RedirectResponse(POOL_URL+'/admin/pool#access='+quote(token,safe=''),headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
    except Exception:raise HTTPException(503,'无法打开账号池，请确认号池服务正在运行')

@app.get('/api/styles')
async def styles():
    records=json.loads(STYLES_FILE.read_text(encoding='utf-8'))
    out=[]
    for x in records:
        number=x.get('number','');group=number.split('-')[0]
        folder=SKILL/'images'/'individual'/group
        image=next((p for p in (folder/(number+'.webp'),folder/(number+'_grid.webp')) if p.exists()),None)
        out.append({'id':number,'name':x.get('generation_name') or number,'group':x.get('group') or '', 'image':'/styles/'+image.relative_to(SKILL/'images').as_posix() if image else None})
    return {'items':out}

@app.get('/api/handdraw-options')
async def handdraw_options():
    result={}
    for key in ['layouts','colors']:
        data=json.loads((STYLE_SKILL/'references'/(key+'.json')).read_text(encoding='utf-8'))
        result[key]=[{'id':x['id'],'name':x.get('name_zh') or x.get('name') or x['id']} for x in data]
    return result

@app.post('/api/upload')
async def upload(file:UploadFile=File(...)):
    from PIL import Image,ImageOps
    import io
    raw=await file.read(12*1024*1024+1)
    if not raw or len(raw)>12*1024*1024:raise HTTPException(400,'图片不能超过 12 MB')
    try:
        with Image.open(io.BytesIO(raw)) as im:
            im=ImageOps.exif_transpose(im);im.thumbnail((2048,2048));im=im.convert('RGB')
            name=uuid.uuid4().hex+'.jpg';im.save(UPLOADS/name,quality=90,optimize=True)
    except Exception:raise HTTPException(400,'请上传有效图片')
    register_upload(name)
    return {'url':'/uploads/'+name}

@app.post('/api/batches')
async def create_batch(body:Batch):
    if body.ratio not in {'1:1','3:2','2:3','4:3','3:4','5:4','4:5','16:9','9:16','21:9'}:raise HTTPException(400,'不支持的画面比例')
    prepared=[]
    try:
        for item in body.items:
            prompt,refs=compile_item(item)
            if not item.prompt.strip():raise ValueError('请填写画面描述。')
            if item.ratio and item.ratio not in {'1:1','3:2','2:3','4:3','3:4','5:4','4:5','16:9','9:16','21:9'}:raise ValueError('不支持的画面比例')
            if item.compiled_prompt is not None:
                if not item.compiled_prompt.strip():raise ValueError('完整提示词不能为空')
                prompt=item.compiled_prompt.strip()
                if item.mode in {'handdraw','portrait'} and item.style:
                    _,decision=style_info(item.style)
                    isolation=REFERENCE_ISOLATION_ZH if detect_language(item.prompt)=='zh' else REFERENCE_ISOLATION_EN
                    if decision.get('use_reference_image') and isolation not in prompt:
                        prompt+='\n'+isolation
                if item.mode=='portrait' and item.style and (item.outfit or item.pose or item.face):
                    redraw=REDRAW_INSTRUCTION_ZH if detect_language(item.prompt)=='zh' else REDRAW_INSTRUCTION_EN
                    if redraw not in prompt:prompt+='\n'+redraw
                if item.mode=='handdraw' and item.references and any(term in (item.prompt+' '+' '.join(x.purpose for x in item.references)).lower() for term in ('转手绘','改图','改风格','转风格','重绘','redraw','style transfer')):
                    redraw=REDRAW_INSTRUCTION_ZH if detect_language(item.prompt)=='zh' else REDRAW_INSTRUCTION_EN
                    if redraw not in prompt:prompt+='\n'+redraw
                if item.mode=='handdraw' and item.layout and resolve_layout(item.layout)['category']!='ecommerce' and not prompt.endswith(GRAPHIC_TEXT_SUFFIX):
                    prompt=prompt.replace(GRAPHIC_TEXT_SUFFIX,'').rstrip()+'\n'+GRAPHIC_TEXT_SUFFIX
            prepared.append((item,prompt,refs))
    except (ValueError,OSError) as e:raise HTTPException(400,str(e))
    batch=uuid.uuid4().hex;now=time.time()
    with conn() as c:
        for item,prompt,refs in prepared:
            c.execute('INSERT INTO jobs (id,batch_id,created_at,updated_at,state,prompt,refs,ratio,style,label,config,owner_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (uuid.uuid4().hex,batch,now,now,'waiting',prompt,json.dumps(refs,ensure_ascii=False),item.ratio or body.ratio,item.style or '',item.label,json.dumps(item.model_dump(),ensure_ascii=False),owner()))
    return {'batch_id':batch,'count':len(prepared)}

@app.post('/api/compile')
async def compile_preview(item:Item):
    try:
        prompt,refs=compile_item(item)
        return {'prompt':prompt,'reference_count':len(refs)}
    except (ValueError,OSError) as e:raise HTTPException(400,str(e))

class Favorite(BaseModel):
    favorite:bool

@app.post('/api/jobs/{job_id}/favorite')
async def favorite(job_id:str,body:Favorite):
    with conn() as c:
        if not c.execute('UPDATE jobs SET favorite=? WHERE id=? AND owner_id=?',(int(body.favorite),job_id,owner())).rowcount:raise HTTPException(404,'作品不存在')
    return {'ok':True}

class Asset(BaseModel):
    url:str
    name:str=Field(min_length=1,max_length=120)
    category:str

@app.get('/api/assets')
async def assets():return {'items':rows('SELECT id,url,name,category,created_at FROM assets WHERE owner_id=? ORDER BY created_at DESC',(owner(),))}

@app.post('/api/assets')
async def add_asset(body:Asset):
    if body.category not in {'人物','衣服','场景','其他'}:raise HTTPException(400,'素材分类无效')
    if not body.url.startswith('/uploads/') or not re.fullmatch(r'/uploads/[a-f0-9]{32}\.jpg',body.url) or not (UPLOADS/Path(body.url).name).is_file():raise HTTPException(400,'请先上传本机图片')
    require_upload(Path(body.url).name)
    with conn() as c:c.execute('INSERT INTO assets(id,url,name,category,created_at,owner_id) VALUES (?,?,?,?,?,?)',(uuid.uuid4().hex,body.url,body.name,body.category,time.time(),owner()))
    return {'ok':True}

@app.delete('/api/assets/{asset_id}')
async def delete_asset(asset_id:str):
    with conn() as c:
        if not c.execute('DELETE FROM assets WHERE id=? AND owner_id=?',(asset_id,owner())).rowcount:raise HTTPException(404,'素材不存在')
    return {'ok':True}

@app.get('/api/batches')
async def batches():
    return {'items':rows('SELECT batch_id,MAX(created_at) AS created_at,COUNT(*) AS count FROM jobs WHERE owner_id=? GROUP BY batch_id ORDER BY created_at DESC LIMIT 100',(owner(),))}

@app.get('/api/jobs')
async def jobs(batch_id:str=''):
    if batch_id and not re.fullmatch(r'[0-9a-f]{32}',batch_id):raise HTTPException(400,'批次编号无效')
    where='WHERE owner_id=?'+(' AND batch_id=?' if batch_id else '')
    direction='ASC' if batch_id else 'DESC'
    order=" ORDER BY CASE WHEN state='completed' THEN 0 WHEN state IN ('waiting','submitting','queued','processing') THEN 1 ELSE 2 END, CASE WHEN state='completed' THEN COALESCE(finished_at,created_at) END "+direction+", created_at DESC,id DESC LIMIT 200"
    return {'items':[public_job(r) for r in rows('SELECT * FROM jobs '+where+order,(owner(),batch_id) if batch_id else (owner(),))]}

@app.get('/api/jobs/{job_id}/details')
async def job_details(job_id:str):
    found=rows('SELECT * FROM jobs WHERE id=? AND owner_id=?',(job_id,owner()))
    if not found:raise HTTPException(404,'作品不存在')
    try:config=json.loads(found[0]['config'])
    except (ValueError,TypeError):config={}
    keys=('outfit','pose','face','style','outfit_id','pose_id','face_id')
    return {'id':job_id,'config':{key:config[key] for key in keys if config.get(key)}}

@app.post('/api/jobs/{job_id}/retry')
async def retry(job_id:str):
    found=rows('SELECT * FROM jobs WHERE id=? AND owner_id=?',(job_id,owner()))
    if not found:raise HTTPException(404,'作品不存在')
    if found[0]['state']!='failed':raise HTTPException(409,'只能重试失败的任务')
    update(job_id,state='waiting',pool_id=None,image_url=None,error=None,finished_at=None)
    return {'ok':True}

@app.get('/api/jobs/{job_id}/image')
async def image(job_id:str):
    if not rows('SELECT id FROM jobs WHERE id=? AND owner_id=?',(job_id,owner())):raise HTTPException(404,'作品不存在')
    return await cached_image(job_id)

async def cached_image(job_id:str):
    found=rows('SELECT * FROM jobs WHERE id=?',(job_id,))
    if not found or found[0]['state']!='completed':raise HTTPException(404,'图片尚未生成')
    if is_expired(found[0]):raise HTTPException(410,'图片已超过 30 天自动清理，生成参数仍可在详情中查看。')
    if not re.fullmatch(r'[a-f0-9]{32}',job_id):raise HTTPException(400,'任务编号无效')
    cached=DATA/'images'/job_id
    if cached.is_file():
        with cached.open('rb') as f:magic=f.read(12)
        content_type='image/png' if magic.startswith(b'\x89PNG') else 'image/webp' if magic.startswith(b'RIFF') else 'image/jpeg'
        return FileResponse(cached,media_type=content_type,headers={'Cache-Control':'private, no-store'})
    url=urlparse(found[0]['image_url'] or '')
    if url.hostname!='127.0.0.1' or url.port!=8000 or not re.fullmatch(r'/api/pool/images/[0-9a-f]{64}\.(png|jpg|webp)',url.path):raise HTTPException(400,'图片地址无效')
    try:
        async with httpx.AsyncClient(timeout=60,trust_env=False) as client:
            response=await pool_request(client,'GET',url.path)
            cached.parent.mkdir(parents=True,exist_ok=True)
            cached.write_bytes(response.content)
            return Response(response.content,media_type=response.headers.get('content-type','application/octet-stream'),headers={'Cache-Control':'private, no-store'})
    except Exception:raise HTTPException(503,'无法读取图片，请检查号池服务')

class DownloadSelection(BaseModel):
    ids:list[str]=Field(min_length=1,max_length=200)

@app.post('/api/jobs/download')
async def download_images(body:DownloadSelection):
    ids=body.ids
    if len(set(ids))!=len(ids) or any(not re.fullmatch(r'[a-f0-9]{32}',x) for x in ids):
        raise HTTPException(400,'选择的图片编号无效')
    marks=','.join('?' for _ in ids)
    found=rows(f'SELECT id,state,finished_at,created_at,expired_at FROM jobs WHERE owner_id=? AND id IN ({marks})',[owner(),*ids])
    if len(found)!=len(ids):raise HTTPException(404,'作品不存在')
    if any(is_expired(x) for x in found):raise HTTPException(410,'部分图片已超过 30 天自动清理，请重新选择。')
    if any(x['state']!='completed' for x in found):
        raise HTTPException(409,'部分图片尚未生成，请刷新后重新选择')
    archive=None
    try:
        DATA.mkdir(parents=True,exist_ok=True)
        fd,path=tempfile.mkstemp(prefix='studio-download-',suffix='.zip',dir=DATA)
        os.close(fd);archive=Path(path)
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as out:
            for i,job_id in enumerate(ids,1):
                await image(job_id)
                cached=DATA/'images'/job_id
                with cached.open('rb') as f:magic=f.read(12)
                ext='.png' if magic.startswith(b'\x89PNG') else '.webp' if magic.startswith(b'RIFF') else '.jpg'
                out.write(cached,f'作品-{i:03d}{ext}')
        return FileResponse(archive,media_type='application/zip',filename='灵感工作室作品.zip',headers={'Cache-Control':'private, no-store'},background=BackgroundTask(archive.unlink,missing_ok=True))
    except HTTPException:
        if archive:archive.unlink(missing_ok=True)
        raise
    except Exception:
        if archive:archive.unlink(missing_ok=True)
        raise HTTPException(500,'打包失败，请稍后重试')

@app.post('/api/jobs/{job_id}/reference')
async def image_reference(job_id:str):
    await image(job_id)
    from PIL import Image,ImageOps
    try:
        name=uuid.uuid4().hex+'.jpg'
        with Image.open(DATA/'images'/job_id) as im:
            im=ImageOps.exif_transpose(im); im.thumbnail((2048,2048)); im.convert('RGB').save(UPLOADS/name,quality=90,optimize=True)
        register_upload(name)
        return {'url':'/uploads/'+name}
    except Exception:raise HTTPException(400,'无法使用这张作品作为参考')

async def handle_job(job_id):
    try:
        found=rows('SELECT * FROM jobs WHERE id=?',(job_id,))
        if not found:return
        job=found[0]
        async with httpx.AsyncClient(timeout=30,trust_env=False) as client:
            pool_id=job['pool_id']
            if not pool_id:
                update(job_id,state='submitting')
                payload={'model':'gpt-image2-1k','prompt':job['prompt'],'aspect_ratio':job['ratio'],'reference_images':json.loads(job['refs']),'gpt_image2_r2_upload':False}
                try:resp=await pool_request(client,'POST','/v1/videos',json=payload)
                except httpx.ConnectError:
                    update(job_id,state='waiting',error='号池暂时无法连接，稍后自动重试。')
                    await asyncio.sleep(15);return
                except httpx.RequestError:
                    update(job_id,state='uncertain',error='提交结果未确认，请查看号池任务记录后再处理。');return
                payload=resp.json();pool_id=payload.get('id') or payload.get('task_id')
                if not pool_id:raise RuntimeError('号池没有返回任务编号')
                update(job_id,state='processing',pool_id=pool_id,error=None)
            while True:
                try:
                    response=await pool_request(client,'GET','/v1/videos/'+pool_id)
                    result=response.json()
                except httpx.RequestError:
                    update(job_id,state='processing',error='号池暂时无法连接，正在等待恢复。')
                    await asyncio.sleep(5);continue
                status=result.get('status')
                if status=='completed':
                    image=result.get('image_url') or result.get('url')
                    if not image:raise RuntimeError('任务已完成，但没有返回图片')
                    update(job_id,state='completed',image_url=image,error=None,finished_at=time.time())
                    try:await cached_image(job_id)
                    except HTTPException:pass
                    return
                if status=='failed':
                    error=result.get('error') or {};raise RuntimeError(str(error.get('message') if isinstance(error,dict) else error)[:180] or '生成未完成')
                update(job_id,state='queued' if status=='queued' else 'processing',error=None)
                await asyncio.sleep(3)
    except asyncio.CancelledError:raise
    except Exception as e:update(job_id,state='failed',error=str(e)[:200])

async def dispatcher():
    while True:
        active_ids={getattr(t,'job_id',None) for t in app_jobs if not t.done()}
        if len(active_ids)<10:
            candidates=rows("SELECT id FROM jobs WHERE state IN ('waiting','queued','processing') ORDER BY created_at ASC")
            for row in candidates:
                if len(active_ids)>=10:break
                if row['id'] in active_ids:continue
                task=asyncio.create_task(handle_job(row['id']));task.job_id=row['id'];app_jobs.add(task);task.add_done_callback(app_jobs.discard);active_ids.add(row['id'])
        await asyncio.sleep(1)
