"""Authenticate the gateway's account context on the loopback hop."""
import hashlib
import hmac
import re
import time


def assertion(owner, method, path, key_path):
    stamp = str(int(time.time()))
    message = '\n'.join((owner, stamp, method.upper(), path)).encode()
    key = bytes.fromhex(key_path.read_text(encoding='ascii').strip())
    if len(key) != 32:
        raise ValueError('Invalid bridge key')
    return {'X-Studio-Owner': owner, 'X-Studio-Time': stamp,
            'X-Studio-Proof': hmac.new(key, message, hashlib.sha256).hexdigest()}


def verified_owner(request, key_path):
    owner = request.headers.get('x-studio-owner', '')
    stamp = request.headers.get('x-studio-time', '')
    proof = request.headers.get('x-studio-proof', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', owner) or not stamp.isdigit():
        raise ValueError('Invalid account context')
    if abs(time.time() - int(stamp)) > 60:
        raise ValueError('Expired account context')
    key = bytes.fromhex(key_path.read_text(encoding='ascii').strip())
    message = '\n'.join((owner, stamp, request.method, request.url.path)).encode()
    expected = hmac.new(key, message, hashlib.sha256).hexdigest()
    if len(key) != 32 or not hmac.compare_digest(proof, expected):
        raise ValueError('Invalid account proof')
    return owner
