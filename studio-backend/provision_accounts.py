"""One-time local credential migration. Never publish data/ to the website."""
import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path

DATA = Path(__file__).resolve().parent / 'data'


def main():
    folder = DATA / 'backups' / ('accounts-' + time.strftime('%Y%m%d-%H%M%S'))
    folder.mkdir(parents=True, exist_ok=True)
    auth_path = DATA / 'remote-auth.json'
    auth_text = auth_path.read_text(encoding='utf-8')
    (folder / 'remote-auth.json').write_text(auth_text, encoding='utf-8')
    for name in ('studio.sqlite3', 'remote.sqlite3'):
        source = sqlite3.connect(DATA / name)
        backup = sqlite3.connect(folder / name)
        source.backup(backup); backup.close(); source.close()
    data = json.loads(auth_text)
    users = data.get('users') or [{**data, 'id': '01', 'enabled': True}]
    created = []
    for name in ('02', '03', '04'):
        if any(x['username'] == name for x in users):
            continue
        password, salt = secrets.token_urlsafe(9), secrets.token_bytes(16)
        users.append({'id': name, 'username': name, 'salt': salt.hex(),
                      'hash': hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex(), 'enabled': True})
        created.append((name, password))
    auth_path.write_text(json.dumps({'version': 2, 'users': users}, indent=2), encoding='utf-8')
    key = DATA / 'bridge-secret.txt'
    if not key.exists():
        key.write_text(secrets.token_hex(32), encoding='ascii')
    credentials = DATA / 'remote-accounts.txt'
    if created:
        with credentials.open('a', encoding='utf-8') as out:
            out.write('新增工作台登录账号（各自记录与私人素材独立）\n')
            for name, password in created:
                out.write('账号：' + name + '    密码：' + password + '\n')
    with sqlite3.connect(DATA / 'remote.sqlite3') as db:
        db.execute('DELETE FROM sessions')
    print('Accounts ready:', ', '.join(x['username'] for x in users), '; old sessions revoked; backups saved locally.')


if __name__ == '__main__':
    main()
