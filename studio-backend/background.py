"""Windowless Windows service supervisor. Run with pythonw.exe at user logon."""
import argparse
import ctypes
from ctypes import wintypes
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
POOL = Path('E:/\u53f7\u6c60')
PYTHON = POOL / 'runtime/fpb-venv/Scripts/python.exe'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def listening(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            return True
    except OSError:
        return False


def launch(args, cwd, name, logs=DATA, pid_dir=DATA, env=None):
    """Redirect to real files and never allocate a console, including for children."""
    logs.mkdir(parents=True, exist_ok=True)
    pid_dir.mkdir(parents=True, exist_ok=True)
    child_env = os.environ.copy()
    child_env.update(PYTHONIOENCODING='utf-8', FPBROWSER2API_APP_ROOT=str(POOL / 'runtime/fpb-app'))
    child_env.update(env or {})
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    with (logs / (name + '.stdout.log')).open('ab') as out, (logs / (name + '.stderr.log')).open('ab') as err:
        process = subprocess.Popen([str(x) for x in args], cwd=str(cwd), env=child_env,
                                   stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                   creationflags=flags, close_fds=True)
    (pid_dir / (name + '.pid')).write_text(str(process.pid), encoding='ascii')
    logging.info('Started %s PID %s', name, process.pid)
    return process


def mutex(name):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateMutexW(None, False, name)
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    if error == 183:
        kernel.CloseHandle(handle)
        return None
    return handle


def process_matches(pid, executable):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        size = wintypes.DWORD(32768)
        path = ctypes.create_unicode_buffer(size.value)
        return bool(kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size))) and Path(path.value) == executable
    finally:
        kernel.CloseHandle(handle)


def uvicorn(module, port, cwd, name, logs=DATA, pid_dir=DATA):
    if not listening(port) and not pid_running(name, PYTHON, pid_dir):
        launch([PYTHON, '-m', 'uvicorn', module, '--host', '127.0.0.1', '--port', str(port),
                '--no-access-log', '--log-level', 'warning'], cwd, name, logs, pid_dir)


def pid_running(name, executable, directory=DATA):
    try:
        return process_matches(int((directory / (name + '.pid')).read_text().strip()), executable)
    except (OSError, ValueError):
        return False


def ensure_services(scope='all'):
    handle = mutex('Local\\InspirationStudioServiceStart')
    if not handle:
        return
    try:
        if scope != 'remote':
            if not listening(9000) and not pid_running('vb', POOL / 'runtime/vb-venv/Scripts/python.exe', POOL / 'runtime'):
                launch([POOL / 'runtime/vb-venv/Scripts/python.exe', POOL / 'checks/run-vb.py'],
                       POOL, 'vb', POOL / 'logs', POOL / 'runtime')
            uvicorn('src.main:app', 8000, POOL / 'sources/fpbrowser2api', 'fpb', POOL / 'logs', POOL / 'runtime')
        if scope == 'pool':
            return
        if scope != 'remote':
            uvicorn('server:app', 8768, ROOT, 'server')
        config = json.loads((DATA / 'remote-config.json').read_text(encoding='utf-8-sig'))
        if config.get('enabled'):
            uvicorn('remote_gateway:app', 8769, ROOT, 'remote')
            # The held mutex, rather than a stale PID, prevents a second tunnel.
            tunnel_handle = mutex('Local\\InspirationStudioTunnel')
            if tunnel_handle:
                try:
                    token = DATA / 'tunnel-token.txt'
                    if token.is_file():
                        # Existing tunnel on migration is adopted by PID and exact executable.
                        pid_file = DATA / 'tunnel.pid'
                        alive = False
                        if pid_file.exists():
                            try:
                                alive = process_matches(int(pid_file.read_text().strip()), POOL / 'runtime/cloudflared/cloudflared.exe')
                            except (OSError, ValueError):
                                pass
                        if not alive:
                            launch([POOL / 'runtime/cloudflared/cloudflared.exe', '--no-autoupdate', 'tunnel',
                                    'run', '--token-file', token], ROOT, 'tunnel',
                                   env={'TUNNEL_TRANSPORT_PROTOCOL': 'http2', 'TUNNEL_EDGE_IP_VERSION': '4'})
                finally:
                    ctypes.WinDLL('kernel32').CloseHandle(wintypes.HANDLE(tunnel_handle))
    finally:
        ctypes.WinDLL('kernel32').CloseHandle(wintypes.HANDLE(handle))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['run', 'once', 'pool', 'remote', 'open'])
    args = parser.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=DATA / 'background.log', encoding='utf-8', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    if args.mode == 'run':
        handle = mutex('Local\\InspirationStudioBackgroundWindowless')
        if not handle:
            return
        (DATA / 'background.pid').write_text(str(os.getpid()), encoding='ascii')
        while True:
            try:
                ensure_services()
            except Exception:
                logging.exception('Service check failed')
            time.sleep(30)
    elif args.mode == 'open':
        # pythonw has no console; the supervisor also uses CREATE_NO_WINDOW for its workers.
        launch([PYTHON.with_name('pythonw.exe'), Path(__file__), 'run'], ROOT, 'background-launch')
        for _ in range(60):
            try:
                with OPENER.open('http://127.0.0.1:8768/api/health', timeout=2) as response:
                    status = json.load(response)
                if status.get('ready') and status.get('service') == 'inspiration-studio':
                    webbrowser.open('http://127.0.0.1:8768/')
                    return
            except Exception:
                pass
            time.sleep(1)
        raise RuntimeError('Studio not ready after 60 seconds; see background.log')
    else:
        ensure_services(args.mode if args.mode in ('pool', 'remote') else 'all')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        logging.exception('Background launcher failed')
        raise
