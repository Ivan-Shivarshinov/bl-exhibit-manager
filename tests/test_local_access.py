from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase, skipUnless
from unittest.mock import patch
import json
import os
import sys

from fastapi.testclient import TestClient
import exhibit.app as web
from exhibit import launch
from exhibit.local_access import LocalAccess, credentials, instance_health
from exhibit.project import Store
from exhibit.samples import make_pdf
from exhibit.translation import Translations


class AccessTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'projects')
        for name, value in [('store', self.store), ('translations', Translations(self.store))]:
            p = patch.object(web, name, value); p.start(); self.addCleanup(p.stop)
        self.access = web.get_access()
        self.http = TestClient(web.app, base_url='http://127.0.0.1:8765')
        self.tls = TestClient(web.app, base_url='https://localhost:8769')
        p = patch.object(web.app.state, 'word_connection', {'configured': True, 'origin': 'https://localhost:8769'}, create=True)
        p.start(); self.addCleanup(p.stop)
        p = patch.object(web.app.state, 'http_port', 8765, create=True); p.start(); self.addCleanup(p.stop)
        self.headers = {'X-Exhibit-Local': '1'}

    def owner(self):
        result = self.http.post('/api/access/session', json={'bootstrap': self.access.bootstrap('http:8765')}, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.http.headers['Authorization'] = 'Bearer '+result.json()['token']
        return result.json()['token']

    def pair(self):
        self.owner()
        request = self.tls.post('/api/access/word/request', json={}, headers=self.headers).json()
        self.assertEqual(self.tls.get('/api/projects').status_code, 401)
        self.assertEqual(self.http.get('/api/access/word/requests').json(), [{'id': request['id'], 'code': request['code']}])
        self.assertEqual(self.http.post('/api/access/word/'+request['id']+'/approve', json={'consent': True}, headers=self.headers).status_code, 200)
        result = self.tls.post('/api/access/word/poll', json={k: request[k] for k in ('id', 'secret')}, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        return result.json()

    def test_anonymous_reads_mutations_aliases_and_downloads_denied(self):
        p = self.store.create('Synthetic private project')
        self.store.upload(p, 'Private.pdf', make_pdf('Synthetic only', [['Private control']]))
        pid, did, eid = p['id'], p['documents'][0]['id'], 'f'*32
        paths = ['/api/projects', f'/api/projects/{pid}', f'/api/projects/{pid}/exports',
                 f'/api/projects/{pid}/exports/{eid}', f'/api/projects/{pid}/documents/{did}/preview',
                 '/api/backups/'+eid+'/download', '/api/library-archives/'+eid+'/download',
                 '/api/libraries', '/api/setup', '/api/access/word/requests', '/api%2Fprojects']
        for client in (self.http, self.tls):
            for path in paths:
                with self.subTest(base=str(client.base_url), path=path): self.assertEqual(client.get(path).status_code, 401)
            for path in ('/api/projects', '/api/demo', '/api/application/stop', '/api/word/rewrite'):
                self.assertEqual(client.post(path, json={'name': 'Attacker'}, headers=self.headers).status_code, 401)
        self.assertEqual(len(self.store.list()), 1)
        for path in ('/', '/word/index.html', '/api/health'):
            response = self.http.get(path)
            self.assertNotIn('set-cookie', response.headers)
            self.assertNotIn('Synthetic private project', response.text)
            self.assertNotIn(self.access.key.hex(), response.text)

    def test_owner_bearer_is_not_a_cookie_and_bootstrap_single_use(self):
        p = self.store.create('Allowed control')
        bootstrap = self.access.bootstrap('http:8765')
        first = self.http.post('/api/access/session', json={'bootstrap': bootstrap}, headers=self.headers)
        self.assertEqual(first.status_code, 200)
        self.assertNotIn('set-cookie', first.headers)
        self.assertEqual(self.http.get('/api/projects').status_code, 401)
        self.http.cookies.set('exhibit-owner', first.json()['token'])
        self.assertEqual(self.http.get('/api/projects').status_code, 401)
        self.http.headers['Authorization'] = 'Bearer '+first.json()['token']
        self.assertEqual(self.http.get('/api/projects').json(), [{'id': p['id'], 'name': p['name']}])
        self.assertEqual(self.http.post('/api/access/session', json={'bootstrap': bootstrap}, headers=self.headers).status_code, 401)
        self.assertEqual(self.tls.get('/api/projects', headers={'Authorization': 'Bearer '+first.json()['token']}).status_code, 401)
        self.assertEqual(self.http.get('/api/projects', headers={'X-Forwarded-Proto': 'https'}).status_code, 403)
        self.assertEqual(self.http.get('/api/projects', headers={'Origin': 'https://other.example'}).status_code, 403)
        self.assertEqual(self.http.get('/api/projects', headers={'Authorization': 'Bearer '+first.json()['token']+'x'}).status_code, 401)

    def test_word_requires_owner_approval_and_is_scoped(self):
        pair = self.pair(); headers = {'Authorization': 'Bearer '+pair['token'], **self.headers}
        self.assertEqual(self.tls.get('/api/projects', headers=headers).status_code, 200)
        for path in ('/api/setup', '/api/access/word/requests', '/api/projects/'+'f'*32, '/api/libraries'):
            self.assertEqual(self.tls.get(path, headers=headers).status_code, 401)
        for path in ('/api/access/word/'+'f'*32+'/approve', '/api/projects', '/api/setup/word/remove', '/api/projects/'+'f'*32+'/upload?kind=translation'):
            self.assertEqual(self.tls.post(path, json={}, headers=headers).status_code, 401)
        self.assertEqual(self.http.get('/api/projects', headers=headers).status_code, 401)
        self.assertEqual(self.http.post('/api/access/word/revoke',json={'consent':True},headers=self.headers).status_code,200)
        self.assertEqual(self.tls.get('/api/projects', headers=headers).status_code, 401)
        self.assertEqual(self.tls.post('/api/access/word/session', json={'grant': pair['grant']}, headers=self.headers).status_code, 401)

    def test_restart_rejects_old_sessions_but_recovers_approved_word(self):
        pair = self.pair(); old_owner = self.owner()
        fresh = LocalAccess(self.store.root)
        web.app.state.local_access = fresh
        self.assertEqual(self.http.get('/api/projects', headers={'Authorization': 'Bearer '+old_owner}).status_code, 401)
        self.assertEqual(self.tls.get('/api/projects', headers={'Authorization': 'Bearer '+pair['token']}).status_code, 401)
        result = self.tls.post('/api/access/word/session', json={'grant': pair['grant']}, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.tls.get('/api/projects', headers={'Authorization': 'Bearer '+result.json()['token']}).status_code, 200)
        self.assertEqual(self.http.post('/api/access/session', json={'bootstrap': self.access.bootstrap('http:8765')}, headers=self.headers).status_code, 401)

    def test_pending_secrets_expiry_and_bounded_requests(self):
        req = self.tls.post('/api/access/word/request', json={}, headers=self.headers).json()
        self.assertEqual(self.tls.post('/api/access/word/poll', json={'id': req['id'], 'secret': 'wrong'}, headers=self.headers).status_code, 401)
        self.assertEqual(self.tls.post('/api/access/word/poll', json={k: req[k] for k in ('id', 'secret')}, headers=self.headers).json(), {'pending': True})
        for _ in range(20): self.access.request_word('https:8769')
        self.assertEqual(len(self.access.requests()), 16)
        self.assertEqual(self.http.post('/api/access/word/request', json={}, headers=self.headers).status_code, 401)
        self.assertEqual(self.tls.post('/api/access/word/request', content=b'x'*4097, headers=self.headers).status_code, 400)
        with patch.object(self.access, 'clock', return_value=self.access.clock()+181): self.assertEqual(self.access.requests(), [])

    def test_expired_and_wrong_purpose_credentials(self):
        token = self.access.bootstrap('http:8765')
        with patch.object(self.access, 'clock', return_value=self.access.clock()+121):
            self.assertEqual(self.http.post('/api/access/session', json={'bootstrap': token}, headers=self.headers).status_code, 401)
        self.assertEqual(self.http.get('/api/projects', headers={'Authorization': 'Bearer '+token}).status_code, 401)
        self.assertEqual(self.tls.post('/api/access/word/session', json={'grant': token}, headers=self.headers).status_code, 401)
        self.assertEqual(self.http.post('/api/access/session', json={'bootstrap': ['not', 'a', 'string']}, headers=self.headers).status_code, 401)

    def test_direct_word_links_handoff_stays_protected_and_has_no_credential(self):
        path = '/api/word/open/'+'/'.join(['f'*32]*3)
        response = self.tls.get(path, follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        from urllib.parse import urlencode
        self.assertEqual(response.headers['location'], 'http://127.0.0.1:8765/?'+urlencode({'open':path}))
        direct = self.http.get(path, follow_redirects=False)
        self.assertEqual(direct.status_code, 307)
        self.assertEqual(direct.headers['location'], '/?'+urlencode({'open':path}))

    def test_private_owner_file_rejects_links_or_shared_permissions(self):
        private = self.access.private
        keyfile = private.folder/'credentials.json'
        if os.name == 'nt':
            alias = self.root/'hardlink.json'; os.link(keyfile, alias)
            with self.assertRaises(ValueError): credentials(self.store.root)
            alias.unlink()
            private.check(keyfile)  # Native SID/DACL inspection was exercised.
        else:
            self.assertEqual(keyfile.stat().st_mode & 0o777, 0o600)
            keyfile.chmod(0o644)
            with self.assertRaises(ValueError): credentials(self.store.root)
            keyfile.chmod(0o600)
            keyfile.rename(private.folder/'original.json')
            keyfile.symlink_to(private.folder/'original.json')
            with self.assertRaises(ValueError): credentials(self.store.root)

    @skipUnless(sys.platform == 'darwin', 'Native extended ACL inspection on macOS')
    def test_mac_extended_allow_acl_is_rejected_even_with_private_mode(self):
        import subprocess
        private = self.access.private
        for path in (private.folder/'credentials.json', private.folder):
            try:
                subprocess.run(['/bin/chmod', '+a', 'everyone allow read', str(path)], check=True, capture_output=True)
                self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)
                with self.assertRaises(ValueError): credentials(self.store.root)
            finally: subprocess.run(['/bin/chmod', '-N', str(path)], check=True, capture_output=True)

    @skipUnless(os.name == 'nt', 'Native Windows principal; POSIX ownership checked separately')
    def test_windows_anonymous_os_principal_cannot_read_owner_credential(self):
        import ctypes
        from ctypes import wintypes
        security = self.access.private.windows
        security.k.GetCurrentThread.restype = wintypes.HANDLE
        security.a.ImpersonateAnonymousToken.argtypes = [wintypes.HANDLE]
        security.a.ImpersonateAnonymousToken.restype = wintypes.BOOL
        security.a.RevertToSelf.restype = wintypes.BOOL
        path = str(self.access.private.folder/'credentials.json')
        self.assertTrue(security.a.ImpersonateAnonymousToken(security.k.GetCurrentThread()))
        try:
            with self.assertRaises(PermissionError):
                with open(path, 'rb') as stream: stream.read()
        finally: self.assertTrue(security.a.RevertToSelf())


class SpoofedInstanceTests(TestCase):
    def test_fake_same_version_health_never_receives_bootstrap_or_opens_browser(self):
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                seen.append(('GET', self.path, dict(self.headers)))
                self.send_response(200); self.end_headers()
                self.wfile.write(json.dumps({'application': 'bl-exhibit-manager', 'version': launch.__version__}).encode())
            def do_POST(self):
                seen.append(('POST', self.path, dict(self.headers)))
                self.send_response(403); self.end_headers()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with TemporaryDirectory() as d:
                root = Path(d); (root/'web/dist').mkdir(parents=True); (root/'web/dist/index.html').write_text('UI')
                with patch('exhibit.project.ROOT', root/'data'), patch.object(launch, 'WORKSPACE', root), patch.object(launch.webbrowser, 'open') as browser, patch.object(launch.subprocess, 'Popen') as spawn:
                    with self.assertRaisesRegex(ValueError, 'принадлежность'): launch.launch(server.server_port)
                    with self.assertRaises(ValueError): launch.stop(server.server_port)
                    browser.assert_not_called(); spawn.assert_not_called()
                for method, path, headers in seen:
                    self.assertEqual(method, 'GET'); self.assertTrue(path.startswith('/api/health?challenge='))
                    self.assertNotIn('Authorization', headers)
        finally: server.shutdown(); server.server_close(); thread.join()
