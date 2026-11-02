"""External account-boundary check, with one private reference generation."""
import io
import json
import re
import sqlite3
import time
from pathlib import Path

import httpx
from PIL import Image

DATA = Path(__file__).resolve().parent / 'data'
RECORD = DATA / 'account-isolation-live-check.json'


def credentials():
    old=(DATA/'remote-login.txt').read_text(encoding='utf-8').splitlines()
    result={next(x.split('：',1)[1] for x in old if x.startswith('用户名：')):
            next(x.split('：',1)[1] for x in old if x.startswith('密码：'))}
    for name,password in re.findall(r'账号：(\S+)\s+密码：(\S+)',(DATA/'remote-accounts.txt').read_text(encoding='utf-8')):
        result[name]=password
    return result


def client_for(name, password):
    client=httpx.Client(base_url='https://api.lanxi1023.com',headers={'Origin':'https://www.lanxi1023.com','X-Studio-Client':'accounts-v1'},timeout=60)
    r=client.post('/auth/login',json={'username':name,'password':password});r.raise_for_status()
    assert r.json()['user']['id']==name
    client.headers.update({'X-Studio-CSRF':r.json()['csrf'],'X-Studio-User':name})
    return client


def main():
    clients={name:client_for(name,password) for name,password in credentials().items()}
    saved=json.loads(RECORD.read_text()) if RECORD.exists() else {}
    def persist():RECORD.write_text(json.dumps(saved,indent=2),encoding='utf-8')
    try:
        a,b=clients['01'],clients['02']
        original=a.get('/api/jobs').json()['items']
        assert len(original)>=73
        private=original[0]['id']
        for name in ('02','03','04'):
            c=clients[name]
            assert c.get('/api/jobs/'+private+'/image').status_code==404
            assert c.get('/api/jobs',params={'batch_id':original[0]['batch_id']}).json()['items']==[]
            assert c.post('/api/jobs/'+private+'/favorite',json={'favorite':True}).status_code==404
            assert c.post('/api/jobs/download',json={'ids':[private]}).status_code==404
            assert c.get('/api/assets').json()['items']==[]
            assert c.get('/api/styles').status_code==200
        print('All four logins and external cross-account access checks passed.',flush=True)
        if not saved.get('upload'):
            image=io.BytesIO();Image.new('RGB',(512,512),(38,105,190)).save(image,format='PNG')
            r=b.post('/api/upload',files={'file':('test-color.png',image.getvalue(),'image/png')});r.raise_for_status()
            saved['upload']=r.json()['url'];persist()
        assert a.get(saved['upload']).status_code==404
        assert b.get(saved['upload']).status_code==200
        if not saved.get('asset'):
            r=b.post('/api/assets',json={'url':saved['upload'],'name':'账号隔离验收素材','category':'其他'});r.raise_for_status()
            saved['asset']=next(x['id'] for x in b.get('/api/assets').json()['items'] if x['url']==saved['upload']);persist()
        assert not any(x['id']==saved['asset'] for x in a.get('/api/assets').json()['items'])
        if not saved.get('batch_id'):
            if saved.get('submitting'):raise RuntimeError('Submission uncertain; review the task instead of repeating it.')
            saved['submitting']=True;persist()
            body={'items':[{'mode':'general','label':'账号隔离验收','prompt':'在纯白色背景上生成一个蓝色陶瓷杯，参考图仅用于蓝色配色，产品摄影，只生成一张。','references':[{'url':saved['upload'],'purpose':'只参考蓝色配色'}]}]}
            r=b.post('/api/batches',json=body);r.raise_for_status();saved.update(r.json());persist()
        deadline=time.monotonic()+600
        while time.monotonic()<deadline:
            r=b.get('/api/jobs',params={'batch_id':saved['batch_id']});r.raise_for_status()
            job=r.json()['items'][0];saved['job_id']=job['id'];persist()
            assert a.get('/api/jobs/'+job['id']+'/image').status_code==404
            assert clients['03'].get('/api/jobs',params={'batch_id':saved['batch_id']}).json()['items']==[]
            print('Account 02 private generation:',job['state'],flush=True)
            if job['state']=='completed':
                r=b.get('/api/jobs/'+job['id']+'/image');r.raise_for_status();assert len(r.content)>1000
                assert b.post('/api/jobs/download',json={'ids':[job['id']]}).content.startswith(b'PK')
                assert a.post('/api/jobs/download',json={'ids':[job['id']]}).status_code==404
                saved['verified_at']=time.time();saved['state']='completed';persist()
                print('Private reference generation and isolated download passed. Test records retained until UI validation.',flush=True)
                return
            if job['state'] in ('failed','uncertain'):raise RuntimeError(job.get('error') or job['state'])
            time.sleep(8)
        raise RuntimeError('Verification timed out')
    finally:
        for client in clients.values():
            try:client.post('/auth/logout')
            finally:client.close()


if __name__=='__main__':main()
