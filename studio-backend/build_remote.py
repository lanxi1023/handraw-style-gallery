"""Build an explicit public-file allowlist; never copy data/ or credentials."""
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'release' / 'studio'


def build(ready=False):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'static').mkdir(exist_ok=True)
    for name in ('network.js', 'remote-boot.js', 'redesign.js', 'redesign.css'):
        shutil.copyfile(ROOT / 'static' / name, OUT / 'static' / name)
    html = (ROOT / 'static/index.html').read_text(encoding='utf-8')
    html = re.sub(r'<script[^>]*src="/catalog/[^>]+></script>', '', html)
    html = re.sub(r'<script[^>]*src="/static/redesign.js[^>]+></script>', '', html)
    html = re.sub(r'<script[^>]*src="/static/network.js[^>]+></script>', '', html)
    html = html.replace('href="/static/', 'href="./static/').replace('href="/"', 'href="./"')
    html = re.sub(r'<a href="/open-pool"[^>]*>.*?</a>', '', html)
    html = html.replace('<nav>', '<nav hidden>')
    html = html.replace('<main>', '<main hidden>')
    html = html.replace('</header>', '<span id="remoteConnection" role="status">正在连接</span><button id="remoteLogout" hidden>退出登录</button></header>')
    gate = '''<section id="remoteGate" class="remote-gate"><form id="remoteLogin" class="remote-login">
<h1>灵感工作室</h1><p>登录后，继续你的创作。</p>
<label for="remoteUser">账号</label><input id="remoteUser" name="username" autocomplete="username" required placeholder="输入账号">
<label for="remotePassword">密码</label><input id="remotePassword" name="password" type="password" autocomplete="current-password" required maxlength="256">
<button id="remoteSubmit" class="primary" type="submit">登录工作台</button>
<p id="remoteStatus" role="status">正在连接生图电脑…</p><button id="remoteReconnect" type="button" class="secondary">重新连接</button>
</form></section>'''
    html = html.replace('</header>', '</header>' + gate)
    html = html.replace('</body>', '<script>window.STUDIO_REMOTE=' + json.dumps({'apiOrigin': 'https://api.lanxi1023.com', 'enabled': ready}) + ';</script><script src="./static/network.js?v=20261102remote2"></script><script src="./static/remote-boot.js?v=20261102remote2"></script></body>')
    (OUT / 'index.html').write_text(html, encoding='utf-8')
    print('Built public frontend only:', OUT)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--ready', action='store_true', help='Only use after HTTPS tunnel and DNS have been verified.')
    build(parser.parse_args().ready)
