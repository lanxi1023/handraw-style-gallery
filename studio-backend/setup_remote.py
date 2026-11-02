"""Prepare local-only credentials. Does not publish a listener or change DNS."""
import hashlib
import json
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'


def prepare():
    DATA.mkdir(exist_ok=True)
    config = DATA / 'remote-config.json'
    if not config.exists():
        config.write_text(json.dumps({'enabled': False, 'api_origin': 'https://api.lanxi1023.com',
                                     'web_origins': ['https://www.lanxi1023.com']}, indent=2), encoding='utf-8')
    auth = DATA / 'remote-auth.json'
    if not auth.exists():
        password, salt = secrets.token_urlsafe(24), secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
        auth.write_text(json.dumps({'username': 'studio', 'salt': salt.hex(), 'hash': digest.hex()}), encoding='utf-8')
        (DATA / 'remote-login.txt').write_text('远程工作台登录\n用户名：studio\n密码：' + password + '\n仅保存在本机，请勿上传到 GitHub。\n', encoding='utf-8')
    print('Remote settings prepared locally; publishing remains disabled.')


if __name__ == '__main__':
    prepare()
