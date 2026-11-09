"""Local owner sessions and explicit Word grants, shared by both listeners."""
from base64 import b64encode, b64decode
from collections import OrderedDict
from hashlib import sha256
import hmac
from http.client import HTTPConnection, HTTPException
import json
import os
from pathlib import Path
import re
import secrets
from threading import RLock
import time

from .private_files import PrivateFiles
from . import __version__

DENIED = 'Доступ к приложению не подтверждён. Запустите приложение обычным способом; для панели Word подтвердите совпадающий код в «Помощь и настройка».'
HEX = re.compile(r'[a-f0-9]{64}')
SESSION_TTL = 12 * 60 * 60
GRANT_TTL = 90 * 24 * 60 * 60


def mac(key, *parts):
    return hmac.new(key, json.dumps(parts, separators=(',', ':')).encode(), sha256).hexdigest()


def sign(key, payload):
    body = b64encode(json.dumps(payload, separators=(',', ':'), sort_keys=True).encode(), altchars=b'-_').decode().rstrip('=')
    return body + '.' + mac(key, 'token-v1', body)


def unpack(key, token, now):
    try:
        if not isinstance(token, str) or len(token) > 2048: raise ValueError(DENIED)
        body, signature = token.split('.')
        if not hmac.compare_digest(signature, mac(key, 'token-v1', body)): raise ValueError(DENIED)
        data = json.loads(b64decode(body + '=' * (-len(body) % 4), altchars=b'-_', validate=True))
        if not isinstance(data, dict) or type(data.get('exp')) is not int or data['exp'] <= now: raise ValueError(DENIED)
        return data
    except (ValueError, TypeError, UnicodeError) as exc: raise ValueError(DENIED) from exc


def credentials(root):
    private = PrivateFiles(root)
    with private.credentials() as (data, stream):
        if data is None:
            data = {'format': 1, 'key': secrets.token_hex(32), 'word_epoch': secrets.token_hex(32)}
            stream.write(json.dumps(data).encode()); stream.flush(); os.fsync(stream.fileno())
        if (not isinstance(data, dict) or set(data) != {'format', 'key', 'word_epoch'} or type(data['format']) is not int or data['format'] != 1
                or any(not isinstance(data[k], str) or not HEX.fullmatch(data[k]) for k in ('key', 'word_epoch'))):
            raise ValueError('Повреждены приватные настройки локального доступа. Проекты не изменены.')
    return private, data


def audience(request):
    # Use the ASGI transport port, not an attacker-supplied Host/proxy header.
    return f"{request.scope['scheme']}:{request.scope['server'][1]}"


class LocalAccess:
    def __init__(self, root, clock=time.time):
        self.private, config = credentials(root)
        self.key = bytes.fromhex(config['key'])
        self.word_epoch = config['word_epoch']
        self.instance = secrets.token_hex(16)
        self.clock = clock
        self.lock = RLock()
        self.pending = OrderedDict()
        self.used = {}

    def proof(self, challenge, port):
        return mac(self.key, 'health-v1', self.instance, port, challenge, __version__)

    def bootstrap(self, aud):
        return sign(self.key, {'role': 'bootstrap', 'aud': aud, 'instance': self.instance,
                              'exp': int(self.clock()) + 120, 'nonce': secrets.token_hex(16)})

    def owner_session(self, token, aud):
        data = unpack(self.key, token, self.clock())
        if (data.get('role') != 'bootstrap' or data.get('aud') != aud or data.get('instance') != self.instance
                or not re.fullmatch('[a-f0-9]{32}', str(data.get('nonce', ''))) or data['exp'] > self.clock() + 120):
            raise ValueError(DENIED)
        with self.lock:
            self.used = {n: exp for n, exp in self.used.items() if exp > self.clock()}
            if data['nonce'] in self.used or len(self.used) >= 256: raise ValueError(DENIED)
            self.used[data['nonce']] = data['exp']
        return self.session('owner', aud)

    def session(self, role, aud, client=None, expiry=None):
        data = {'role': role, 'aud': aud, 'instance': self.instance,
                'exp': min(int(self.clock()) + SESSION_TTL, expiry or int(self.clock()) + SESSION_TTL)}
        if role == 'word': data.update(client=client, epoch=self.word_epoch)
        return sign(self.key, data)

    def role(self, token, aud):
        try:
            data = unpack(self.key, token, self.clock())
            if data.get('aud') != aud or data.get('instance') != self.instance or data['exp'] > self.clock() + SESSION_TTL: return None
            if data.get('role') == 'owner': return 'owner'
            if data.get('role') == 'word' and aud.startswith('https:') and data.get('epoch') == self.word_epoch: return 'word'
        except ValueError: pass
        return None

    def _expire(self):
        for pid in list(self.pending):
            if self.pending[pid]['expires'] <= self.clock(): del self.pending[pid]

    def request_word(self, aud):
        if not aud.startswith('https:'): raise ValueError(DENIED)
        with self.lock:
            self._expire()
            if len(self.pending) >= 16: self.pending.popitem(last=False)
            pid, secret = secrets.token_hex(16), secrets.token_urlsafe(32)
            codes = {p['code'] for p in self.pending.values()}
            code = secrets.token_hex(4).upper()
            while code in codes: code = secrets.token_hex(4).upper()
            self.pending[pid] = {'secret': sha256(secret.encode()).digest(), 'code': code, 'aud': aud,
                                 'expires': self.clock() + 180, 'approved': False}
            return {'id': pid, 'secret': secret, 'code': code[:4] + '-' + code[4:], 'expires_in': 180}

    def requests(self):
        with self.lock:
            self._expire()
            return [{'id': pid, 'code': p['code'][:4] + '-' + p['code'][4:]}
                    for pid, p in self.pending.items() if not p['approved']]

    def approve_word(self, pid):
        with self.lock:
            self._expire()
            if not isinstance(pid, str) or pid not in self.pending: raise ValueError('Код панели истёк. Дождитесь нового кода в Word.')
            self.pending[pid]['approved'] = True

    def poll_word(self, pid, secret, aud):
        with self.lock:
            self._expire()
            p = self.pending.get(pid) if isinstance(pid, str) else None
            if (not p or not isinstance(secret, str) or len(secret) > 128 or p['aud'] != aud
                    or not hmac.compare_digest(p['secret'], sha256(secret.encode()).digest())): raise ValueError(DENIED)
            if not p['approved']: return {'pending': True}
            grant = sign(self.key, {'role': 'word-grant', 'aud': aud, 'epoch': self.word_epoch,
                                   'client': pid, 'exp': int(self.clock()) + GRANT_TTL})
            del self.pending[pid]
            return {'grant': grant, 'token': self.word_session(grant, aud)}

    def word_session(self, grant, aud):
        data = unpack(self.key, grant, self.clock())
        if (not aud.startswith('https:') or data.get('role') != 'word-grant' or data.get('aud') != aud
                or data.get('epoch') != self.word_epoch or data['exp'] > self.clock() + GRANT_TTL): raise ValueError(DENIED)
        return self.session('word', aud, data.get('client'), data['exp'])

    def revoke_word(self):
        with self.lock, self.private.credentials() as (data, stream):
            if data['key'] != self.key.hex(): raise ValueError(DENIED)
            data['word_epoch'] = secrets.token_hex(32)
            stream.seek(0); stream.write(json.dumps(data).encode()); stream.truncate(); stream.flush(); os.fsync(stream.fileno())
            self.word_epoch = data['word_epoch']; self.pending.clear()


def instance_health(port, root):
    """Prove ownership without sending a credential to an occupied loopback port."""
    _, config = credentials(root)
    challenge = secrets.token_hex(32)
    connection = HTTPConnection('127.0.0.1', port, timeout=2)
    try:
        connection.request('GET', '/api/health?challenge=' + challenge)
        response = connection.getresponse()
        if response.status != 200: return None
        info = json.loads(response.read(65536))
        if not isinstance(info, dict) or info.get('application') != 'bl-exhibit-manager': return None
        if info.get('version') != __version__: return info
        expected = mac(bytes.fromhex(config['key']), 'health-v1', info.get('instance'), port, challenge, __version__)
        if not isinstance(info.get('proof'), str) or not hmac.compare_digest(expected, info['proof']):
            raise ValueError('Порт занят экземпляром, принадлежность которого не подтверждена. Остановите прежнее приложение его собственным Stop; полномочие не передано.')
        return info
    except (OSError, ValueError, HTTPException) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith('Порт занят'): raise
        return None
    finally: connection.close()


def owner_bootstrap(root, port, info):
    _, config = credentials(root)
    return sign(bytes.fromhex(config['key']), {'role': 'bootstrap', 'aud': f'http:{port}',
                'instance': info['instance'], 'exp': int(time.time()) + 120, 'nonce': secrets.token_hex(16)})


def owner_headers(base, root):
    """Native/verification clients use the same private owner bootstrap as launch."""
    from urllib.parse import urlsplit
    url = urlsplit(base)
    if url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port: raise ValueError(DENIED)
    info = instance_health(url.port, root)
    if not info or info.get('version') != __version__: raise ValueError('Сначала запустите соответствующее приложение.')
    token = owner_bootstrap(root, url.port, info)
    connection = HTTPConnection('127.0.0.1', url.port, timeout=5)
    try:
        connection.request('POST', '/api/access/session', body=json.dumps({'bootstrap': token}).encode(),
                           headers={'X-Exhibit-Local': '1', 'Content-Type': 'application/json'})
        response = connection.getresponse(); data = json.loads(response.read(65536))
        if response.status != 200: raise ValueError(DENIED)
        return {'X-Exhibit-Local': '1', 'Authorization': 'Bearer ' + data['token']}
    finally: connection.close()
