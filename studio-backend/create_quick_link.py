"""Provision or revoke a private shortcut locally. Never publish data/ or its URL."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
import remote_gateway as gateway


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--user-id',default='01')
    parser.add_argument('--rotate',action='store_true')
    parser.add_argument('--revoke',action='store_true')
    args=parser.parse_args()
    _,users=gateway.auth_records()
    user=next((x for x in users if x['id']==args.user_id and x.get('enabled',True)),None)
    if not user:raise RuntimeError('Enabled account not found')
    private=gateway.DATA/('quick-link-'+args.user_id+'.txt')
    if args.revoke:
        with gateway.database() as db:db.execute('UPDATE shortcuts SET enabled=0 WHERE user_id=?',(args.user_id,))
        print('Private shortcut and its sessions revoked.')
        return
    if private.exists() and not args.rotate:
        url=private.read_text(encoding='utf-8').strip()
        token=url.split('#access=',1)[-1]
        with gateway.database() as db:
            existing=db.execute('SELECT user_id FROM shortcuts WHERE digest=? AND enabled=1',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not existing or existing['user_id']!=args.user_id:raise RuntimeError('Existing shortcut is inactive; use --rotate to replace it')
    else:
        token=secrets.token_urlsafe(32)
        with gateway.database() as db:
            if args.rotate:db.execute('UPDATE shortcuts SET enabled=0 WHERE user_id=?',(args.user_id,))
            db.execute('INSERT INTO shortcuts(digest,user_id,created_at) VALUES (?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),args.user_id,time.time()))
        origin=json.loads(gateway.CONFIG.read_text(encoding='utf-8'))['web_origins'][0]
        url=origin+'/studio/#access='+token
        private.write_text(url,encoding='utf-8')
    visible=Path('E:/\u53f7\u6c60')/'\u5de5\u4f5c\u53f0\u5feb\u6377\u94fe\u63a5.txt'
    visible.write_text('个人快捷入口（账号 '+user['username']+'）\n'+url+'\n\n完整链接相当于登录凭证，请勿转发。进入后地址栏会自动隐藏凭证。\n',encoding='utf-8-sig')
    if os.name=='nt':
        who=subprocess.check_output(['whoami'],text=True).strip()
        for path in (private,visible):
            subprocess.run(['icacls',str(path),'/inheritance:r','/grant:r',who+':F','SYSTEM:F'],check=True,stdout=subprocess.DEVNULL)
    print('Private shortcut saved locally; raw token omitted from logs.')


if __name__=='__main__':main()
