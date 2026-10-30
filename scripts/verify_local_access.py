"""Isolated HTTP/TLS and headless browser acceptance, without Office/system trust."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from exhibit.launch import browser_entry
from exhibit.local_access import instance_health, owner_headers
from exhibit.samples import make_pdf


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--output', required=True)
    args = parser.parse_args(); out = Path(args.output).resolve(); out.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='bl-access-') as directory:
        root = Path(directory); data = root/'data'; home = root/'home'; home.mkdir()
        with socket.socket() as s: s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
        with socket.socket() as s: s.bind(('127.0.0.1', 0)); tls_port = s.getsockname()[1]
        base, word = f'http://127.0.0.1:{port}', f'https://localhost:{tls_port}'
        env = {**os.environ, 'EXHIBIT_DATA_DIR': str(data), 'HOME': str(home), 'USERPROFILE': str(home)}
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        log = (out/'server.log').open('w', encoding='utf-8')
        def start():
            proc = subprocess.Popen([sys.executable, '-m', 'exhibit.launch', '--serve', '--port', str(port), '--quiet'],
                                    cwd=ROOT, env=env, stdout=log, stderr=log, **flags)
            for _ in range(150):
                if instance_health(port, data): return proc
                if proc.poll() is not None: raise AssertionError('Isolated server exited')
                time.sleep(.1)
            proc.terminate(); raise AssertionError('Isolated server timeout')
        proc = start()
        try:
            client = httpx.Client(base_url=base, headers=owner_headers(base, data), timeout=30)
            p = client.post('/api/projects', json={'name': 'Synthetic access control'}).json()
            p = client.post('/api/projects/'+p['id']+'/upload?mode=passthrough&name=Annex12.pdf', content=make_pdf('Synthetic target', [['Synthetic only']])).json()
            pid, did = p['id'], p['documents'][0]['id']
            assert client.post('/api/setup/word', json={'port': tls_port}).status_code == 200
            for origin in (base, word):
                with httpx.Client(verify=False) as anonymous:  # Fixture certificate only; no system trust changes.
                    assert anonymous.get(origin+'/api/projects').status_code == 401
                    assert anonymous.post(origin+'/api/projects', json={'name': 'Denied'}, headers={'X-Exhibit-Local': '1'}).status_code == 401
            entries = out/'browser-entries.json'
            # Contains only owner-private file URLs, never credentials or project data.
            entries.write_text(json.dumps({'first': browser_entry(port, data)}), encoding='utf-8')
            browser_env = dict(os.environ)
            if os.name == 'nt':
                for key in ('TEMP', 'TMP'):
                    if browser_env.get(key, '').startswith('\\\\?\\'): browser_env[key] = browser_env[key][4:]
            control = subprocess.Popen(['node', str(ROOT/'scripts/local_access_ui.cjs')], cwd=ROOT,
                                       env={**browser_env, 'ACCESS_QA_URL': base, 'ACCESS_QA_WORD': word,
                                            'ACCESS_QA_ENTRY': str(entries), 'ACCESS_QA_OUTPUT': str(out),
                                            'ACCESS_QA_PID': pid, 'ACCESS_QA_DID': did}, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding='utf-8', **flags)
            for line in control.stdout:
                if line.strip() == 'RESTART_SERVER':
                    assert client.post('/api/application/stop', json={}).status_code == 200
                    proc.wait(timeout=15); proc = start()
                    client.close(); client = httpx.Client(base_url=base, headers=owner_headers(base, data), timeout=30)
                    entries.write_text(json.dumps({'first': browser_entry(port, data), 'restarted': True}), encoding='utf-8')
                else: print(line.rstrip(), flush=True)
            assert control.wait(timeout=60) == 0
            assert len(client.get('/api/projects').json()) == 1
            assert client.post('/api/application/stop', json={}).status_code == 200
            proc.wait(timeout=15); client.close()
        finally:
            if proc.poll() is None: proc.terminate(); proc.wait(timeout=15)
            log.close()


if __name__ == '__main__': main()
