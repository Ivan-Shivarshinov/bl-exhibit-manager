import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from exhibit import launch, word_tls


class OptionalWordConnection(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = FastAPI()

    def test_no_configuration_needs_no_certificate_or_listener(self):
        with patch.object(word_tls.uvicorn, 'Server') as server:
            self.assertIsNone(word_tls.start_optional(self.app, self.root, 8765))
            server.assert_not_called()
        self.assertEqual(self.app.state.word_connection, {'configured': False})

    def test_bad_configuration_is_preserved_and_reported_without_raising(self):
        values = ['invalid json', 'null', '[]', '{}', json.dumps({'port': 8765}),
                  json.dumps({'port': 8769, 'cert': str(self.root/'missing.crt'), 'key': str(self.root/'missing.key')})]
        path = self.root/'word-connection.json'
        for value in values:
            with self.subTest(value=value):
                path.write_text(value, encoding='utf-8')
                with self.assertLogs('exhibit.word_tls', level='WARNING'):
                    self.assertIsNone(word_tls.start_optional(self.app, self.root, 8765))
                self.assertFalse(self.app.state.word_connection['configured'])
                self.assertIn('DOCX', self.app.state.word_connection['message'])
                self.assertEqual(path.read_text('utf-8'), value)

    def test_listener_failure_leaves_connection_unconfigured(self):
        for error in [OSError('certificate unavailable'), ValueError('HTTPS port in use')]:
            with self.subTest(error=error), patch.object(word_tls, 'start', side_effect=error), self.assertLogs('exhibit.word_tls', level='WARNING'):
                self.assertIsNone(word_tls.start_optional(self.app, self.root, 8765))
                self.assertFalse(self.app.state.word_connection['configured'])

    def test_success_keeps_listener_for_graceful_shutdown(self):
        listener = (MagicMock(), MagicMock())
        def started(app, root, port):
            app.state.word_connection = {'configured': True, 'origin': 'https://localhost:8769'}
            return listener
        with patch.object(word_tls, 'start', side_effect=started):
            self.assertIs(word_tls.start_optional(self.app, self.root, 8765), listener)
        self.assertTrue(self.app.state.word_connection['configured'])

    def test_http_server_still_runs_when_optional_configuration_is_broken(self):
        (self.root/'word-connection.json').write_text('{bad', encoding='utf-8')
        with patch('exhibit.project.ROOT', self.root), patch('logging.basicConfig'), patch.object(word_tls.uvicorn, 'Server') as server, self.assertLogs('exhibit.word_tls', level='WARNING'):
            launch.serve(8765)
        server.return_value.run.assert_called_once()
        self.assertTrue(server.return_value.should_exit)
