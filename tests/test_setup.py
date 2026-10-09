import json
from pathlib import Path
import socket
import ssl
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from exhibit import diagnostics, word_setup, word_tls
from exhibit.app import app, store


class SetupTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = FastAPI()
        self.app.state.http_port = 8765
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            self.port = s.getsockname()[1]

    def test_generation_repeat_and_existing_developer_configuration(self):
        word_setup.prepare(self.root, 8765, self.port)
        config, cert = word_setup.configuration(self.root)
        self.assertEqual(cert.extensions.get_extension_for_class(word_setup.x509.SubjectAlternativeName).value.get_values_for_type(word_setup.x509.DNSName), ['localhost'])
        original = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        word_setup.prepare(self.root, 8765, self.port + 1)
        self.assertEqual(original, {p: p.read_bytes() for p in original})
        other = self.root / 'developer'; other.mkdir()
        word_setup.configure(other, config['cert'], config['key'], self.port)
        before = (other/'word-connection.json').read_bytes()
        word_setup.prepare(other, 8765)
        self.assertEqual(before, (other/'word-connection.json').read_bytes())
        self.assertFalse(word_setup.status(other, self.app)['generated'])
        with self.assertRaisesRegex(ValueError, 'не создан приложением'):
            word_setup.prepare(other,8765,renew=True)
        self.assertEqual(before,(other/'word-connection.json').read_bytes())

    def test_occupied_port_and_bad_config_preserved(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', self.port)); s.listen()
            with self.assertRaisesRegex(ValueError, 'занят'): word_setup.prepare(self.root, 8765, self.port)
        self.assertFalse((self.root/'word-connection.json').exists())
        (self.root/'word-connection.json').write_text('not json', 'utf-8')
        with self.assertRaises(ValueError): word_setup.prepare(self.root, 8765, self.port)
        self.assertEqual(word_setup.status(self.root, self.app)['state'], 'error')
        self.assertEqual((self.root/'word-connection.json').read_text('utf-8'), 'not json')

    def test_automatic_port_when_default_is_used_by_http_or_another_process(self):
        word_setup.prepare(self.root, 8769)
        config, _ = word_setup.configuration(self.root, 8769)
        self.assertNotEqual(config['port'], 8769)
        second = self.root / 'second'
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', 8769))
            except OSError:
                pass  # An existing listener already exercises occupied-default handling.
            word_setup.prepare(second, 8765)
        self.assertNotEqual(word_setup.configuration(second)[0]['port'], 8769)

    def test_live_listener_without_system_trust_then_explicit_test_trust(self):
        @self.app.get('/api/health')
        def health(): return {'application': 'bl-exhibit-manager'}
        word_setup.prepare(self.root, 8765, self.port)
        listener = word_tls.start_optional(self.app, self.root, 8765)
        self.assertIsNotNone(listener)
        try:
            self.assertEqual(word_setup.status(self.root, self.app, True)['state'], 'needs_trust')
            config, _ = word_setup.configuration(self.root)
            context = ssl.create_default_context(cafile=config['cert'])
            # This supplies test-client trust, not proof of real system trust.
            from exhibit.word_install import NativeSettings
            with patch.object(word_setup, 'trust_context', return_value=context), patch.object(NativeSettings, 'trusted', return_value=True):
                self.assertEqual(word_setup.status(self.root, self.app, True)['state'], 'trusted')
        finally:
            listener[0].should_exit = True
            listener[1].join(5)
        self.assertFalse(listener[1].is_alive())

    def test_windows_intermediate_store_is_not_a_trust_anchor(self):
        @self.app.get('/api/health')
        def health(): return {'application': 'bl-exhibit-manager'}
        word_setup.prepare(self.root, 8765, self.port)
        _, cert = word_setup.configuration(self.root)
        entry = (cert.public_bytes(word_setup.serialization.Encoding.DER), 'x509_asn', True)
        listener = word_tls.start_optional(self.app, self.root, 8765)
        # Mock only TLS trust: the installer must keep the real host platform.
        from exhibit.word_install import manager
        manager(self.app, self.root)
        try:
            with patch.object(word_setup.sys, 'platform', 'win32'):
                # Reproduce automatic installation into Intermediate CAs: Python's
                # default context trusted this, whereas Windows browsers did not.
                with patch.object(ssl, 'enum_certificates', create=True,
                                  side_effect=lambda store: [entry] if store == 'CA' else []) as stores:
                    value = word_setup.status(self.root, self.app, True)
                    self.assertEqual(value['state'], 'needs_trust')
                    self.assertIn('Повторите настройку', value['message'])
                    stores.assert_called_once_with('ROOT')
                with patch.object(ssl, 'enum_certificates', create=True, return_value=[entry]):
                    self.assertEqual(word_setup.status(self.root, self.app, True)['state'], 'trusted')
                with patch.object(ssl, 'enum_certificates', create=True,
                                  return_value=[(entry[0], entry[1], {'1.3.6.1.5.5.7.3.4'})]):
                    self.assertEqual(word_setup.status(self.root, self.app, True)['state'], 'needs_trust')
        finally:
            listener[0].should_exit = True
            listener[1].join(5)

    def test_non_windows_uses_existing_tls_trust(self):
        with patch.object(word_setup.sys, 'platform', 'darwin'), patch.object(ssl, 'create_default_context') as create:
            self.assertIs(word_setup.trust_context(), create.return_value)
            create.assert_called_once_with()

    def test_windows_same_subject_roots_verify_only_the_exact_trusted_certificate(self):
        @self.app.get('/api/health')
        def health(): return {'application':'bl-exhibit-manager'}
        word_setup.prepare(self.root, 8765, self.port)
        _, certificate = word_setup.configuration(self.root)
        other = self.root/'other'; word_setup.prepare(other, 8765)
        _, previous = word_setup.configuration(other)
        self.assertEqual(certificate.subject, previous.subject)
        entry = lambda c:(c.public_bytes(word_setup.serialization.Encoding.DER), 'x509_asn', True)
        listener = word_tls.start_optional(self.app, self.root, 8765)
        from exhibit.word_install import manager
        manager(self.app, self.root)
        try:
            with patch.object(word_setup.sys, 'platform', 'win32'), patch.object(ssl, 'enum_certificates', create=True, return_value=[entry(previous),entry(certificate)]):
                context = word_setup.trust_context(certificate)
                self.assertEqual(context.cert_store_stats()['x509_ca'], 1)
                self.assertEqual(word_setup.status(self.root, self.app, True)['state'], 'trusted')
            with patch.object(word_setup.sys, 'platform', 'win32'), patch.object(ssl, 'enum_certificates', create=True, return_value=[entry(previous)]):
                self.assertEqual(word_setup.status(self.root, self.app, True)['state'], 'needs_trust')
        finally:
            listener[0].should_exit = True; listener[1].join(5)

    def test_summary_does_not_invoke_cli_or_mutate_files(self):
        with patch.object(store, 'root', self.root), patch.object(diagnostics.translation_cli, 'status') as cli:
            client = TestClient(app, base_url='http://localhost')
            response = client.get('/api/setup')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['components']['word']['state'], 'not_configured')
            self.assertEqual(response.json()['components']['claude']['state'], 'unchecked')
            cli.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_report_allowlist_rejects_private_data(self):
        secret = 'PRIVATE_DOCUMENT_NAME_PATH_TOKEN_KEY'
        components = {'converter': {'state': 'available', 'executable': secret},
                      'word': {'state': 'running', 'origin': secret, 'key': secret},
                      'claude': {'state': secret, 'message': secret, 'version': secret},
                      secret: {'state': 'available'}}
        result = json.dumps(diagnostics.report(components))
        self.assertNotIn(secret, result)
        self.assertEqual(diagnostics.report(components)['components']['claude'], {'state': 'error'})

    def test_missing_converter_and_provider_login_are_actionable(self):
        with patch.object(diagnostics.main_pdf, 'available', return_value={'available':False}):
            self.assertEqual(diagnostics.converter()['state'], 'missing')
        with patch.object(diagnostics.translation_cli, 'executable', side_effect=ValueError('private/path')):
            self.assertEqual(diagnostics.provider('claude')['state'], 'missing')
        with patch.object(diagnostics.translation_cli, 'executable', return_value='private/path'), patch.object(diagnostics.translation_cli, 'status', return_value={'ready':False,'state':'needs_login','message':'SECRET'}):
            value = diagnostics.provider('codex')
            self.assertEqual(value['state'], 'needs_login')
            self.assertNotIn('SECRET', json.dumps(value))

    def test_setup_api_certificate_download_and_http_survives_failed_listener(self):
        with patch.object(store, 'root', self.root), patch.object(word_tls, 'start_optional', return_value=None):
            client = TestClient(app, base_url='http://localhost')
            response = client.post('/api/setup/word', json={'port':self.port}, headers={'X-Exhibit-Local':'1'})
            self.assertEqual(response.status_code, 200)
            public = client.get('/api/setup/word/certificate')
            self.assertIn(b'BEGIN CERTIFICATE', public.content)
            self.assertNotIn(b'PRIVATE KEY', public.content)
            self.assertEqual(client.get('/api/health').status_code, 200)
            self.assertIn('localhost', client.get('/api/word/manifest').text)
            self.assertEqual(client.post('/api/setup/word', json={}).status_code, 403)
