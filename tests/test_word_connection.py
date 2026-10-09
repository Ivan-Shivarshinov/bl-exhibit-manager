from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient
from exhibit.app import app
from exhibit.word_connection import WordPanels
from exhibit import diagnostics


class WordConnectionTests(TestCase):
    def setUp(self):
        self.now = 100
        self.panels = WordPanels(clock=lambda: self.now)

    def open(self, supported=True):
        return self.panels.update({'action': 'open', 'host': 'Word', 'supported': supported})['token']

    def test_presence_expires_even_when_https_keeps_running(self):
        token = self.open()
        self.assertEqual(self.panels.status()['state'], 'connected')
        self.now += 40
        self.panels.update({'action': 'ping', 'token': token})
        self.now += 40
        self.assertEqual(self.panels.status()['state'], 'connected')
        self.now += 6
        self.assertEqual(self.panels.status()['state'], 'disconnected')
        with self.assertRaises(ValueError):
            self.panels.update({'action': 'ping', 'token': token})

    def test_two_panels_and_incompatible_word_are_distinguished(self):
        first, second = self.open(), self.open()
        self.panels.update({'action': 'close', 'token': first})
        self.assertEqual(self.panels.status()['state'], 'connected')
        self.panels.update({'action': 'close', 'token': second})
        self.assertEqual(self.panels.status()['state'], 'disconnected')
        unsupported = self.open(False)
        self.assertEqual(self.panels.status()['state'], 'unsupported')
        self.assertIn('Office 2024', self.panels.status()['message'])
        self.panels.update({'action': 'close', 'token': unsupported})
        self.panels.update({'action': 'close', 'token': unsupported})  # Retry is harmless.

    def test_invalid_hosts_and_private_fields_never_create_presence(self):
        for body in (None, [], {}, {'action':'open','host':'Browser','supported':True},
                     {'action':'open','host':'Word','supported':'yes'},
                     {'action':'open','host':'Word','supported':True,'document':'PRIVATE'}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.panels.update(body)
        self.assertEqual(self.panels.status()['state'], 'waiting')
        self.assertEqual(self.panels.sessions, {})

    def test_bounded_sessions_and_restart_clear_old_tokens(self):
        for _ in range(self.panels.limit):
            self.open()
        with self.assertRaises(ValueError): self.open()
        old = next(iter(self.panels.sessions))
        self.panels.clear()
        with self.assertRaises(ValueError): self.panels.update({'action':'ping','token':old})
        self.open()

    def test_api_requires_current_https_listener_and_same_origin(self):
        origin = 'https://localhost:8769'
        payload = {'action':'open','host':'Word','supported':True}
        headers = {'X-Exhibit-Local':'1'}
        with patch.object(app.state, 'word_connection', {'configured':True, 'origin':origin}, create=True), patch.object(app.state, 'word_panels', self.panels):
            for base in ('http://localhost:8769', 'https://localhost:8770'):
                self.assertEqual(TestClient(app, base_url=base).post('/api/word/connection', json=payload, headers=headers).status_code, 403)
            client = TestClient(app, base_url=origin)
            self.assertEqual(client.post('/api/word/connection', json=payload).status_code, 403)
            self.assertEqual(client.post('/api/word/connection', json=payload, headers={**headers,'Origin':'https://example.com'}).status_code, 403)
            self.assertEqual(client.get('/api/health').status_code, 200)
            self.assertEqual(self.panels.status()['state'], 'waiting')
            token = client.post('/api/word/connection', json=payload, headers=headers).json()['token']
            self.assertEqual(self.panels.status()['state'], 'connected')
            self.assertEqual(client.post('/api/word/connection', json={'action':'close','token':token}, headers=headers).status_code, 200)
            self.assertEqual(self.panels.status()['state'], 'disconnected')

    def test_support_report_includes_states_without_session_tokens(self):
        token = self.open()
        report = diagnostics.report({'word':{'state':'running','panel':{**self.panels.status(),'token':token,'document':'PRIVATE'}}})
        self.assertEqual(report['components']['word'], {'state':'running','panel_state':'connected'})
        self.assertNotIn(token, str(report))
        self.assertNotIn('PRIVATE', str(report))
