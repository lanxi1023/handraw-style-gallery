"""One real external generation. Reruns reuse the saved batch to avoid duplicates."""
import json
import time
from pathlib import Path

import httpx

DATA = Path(__file__).resolve().parent / 'data'
RECORD = DATA / 'remote-live-check.json'
ORIGIN = 'https://www.lanxi1023.com'
API = 'https://api.lanxi1023.com'


def main():
    credential = (DATA / 'remote-login.txt').read_text(encoding='utf-8').splitlines()
    password = next(x.split('：', 1)[1] for x in credential if x.startswith('密码：'))
    username = next(x.split('：', 1)[1] for x in credential if x.startswith('用户名：'))
    with httpx.Client(base_url=API, headers={'Origin': ORIGIN}, timeout=60) as client:
        assert client.get('/api/jobs').status_code == 401
        response = client.post('/auth/login', json={'username': username, 'password': password})
        response.raise_for_status()
        client.headers['X-Studio-CSRF'] = response.json()['csrf']
        assert client.get('/api/styles').status_code == 200
        assert client.get('/open-pool').status_code == 404
        if RECORD.exists():
            saved = json.loads(RECORD.read_text(encoding='utf-8'))
        else:
            body = {'ratio': '1:1', 'items': [{'mode': 'general', 'prompt': '一只蓝色陶瓷杯放在纯白色背景上，简洁自然的产品摄影，只生成一张图片。', 'label': '远程连接验收', 'references': []}]}
            # Record submission intent first. Never repeat an uncertain POST.
            RECORD.write_text(json.dumps({'state': 'submitting'}), encoding='utf-8')
            response = client.post('/api/batches', json=body)
            response.raise_for_status()
            saved = response.json()
            RECORD.write_text(json.dumps(saved), encoding='utf-8')
        if not saved.get('batch_id'):
            raise RuntimeError('Submission is uncertain. Check task records before attempting again.')
        print('External login and task submission passed. Batch:', saved['batch_id'], flush=True)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            response = client.get('/api/jobs', params={'batch_id': saved['batch_id']})
            response.raise_for_status()
            jobs = response.json()['items']
            if jobs:
                job = jobs[0]
                print('Remote generation:', job['state'], flush=True)
                if job['state'] == 'completed':
                    image = client.get('/api/jobs/' + job['id'] + '/image')
                    image.raise_for_status()
                    assert image.headers['content-type'].startswith('image/') and len(image.content) > 1000
                    archive = client.post('/api/jobs/download', json={'ids': [job['id']]})
                    archive.raise_for_status()
                    assert archive.content.startswith(b'PK')
                    saved.update({'state': 'completed', 'job_id': job['id'], 'image_bytes': len(image.content), 'verified_at': time.time()})
                    RECORD.write_text(json.dumps(saved), encoding='utf-8')
                    client.post('/auth/logout').raise_for_status()
                    assert client.get('/api/jobs').status_code == 401
                    print('External generation, image retrieval, ZIP download and logout passed.', flush=True)
                    return
                if job['state'] in ('failed', 'uncertain'):
                    raise RuntimeError(str(job.get('error') or job['state']))
            time.sleep(8)
        raise RuntimeError('Remote generation did not finish within the verification window.')


if __name__ == '__main__':
    main()
