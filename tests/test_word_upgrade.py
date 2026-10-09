"""Real isolated registrations; trust and the Word host are simulated."""
import json
import sys
import threading
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi import FastAPI
from exhibit import diagnostics, word_setup
from exhibit.word_connection import WordPanels
from exhibit.word_install import ADDIN_ID, Installer, NativeSettings, Cancelled


class IsolatedSettings(NativeSettings):
    def __init__(self, root, platform):
        super().__init__(platform=platform, home=root/'home', registry='Software\\BLExhibitManager\\Tests\\' + uuid.uuid4().hex)
        self.has_trust = False
        self.calls = []
        self.failure = None
        self.on_trust = None

    def check_word(self): pass
    def trusted(self, cert, cancel=None): return self.has_trust
    def certificate_present(self, cert): return self.has_trust
    def install_trust(self, cert, cancel):
        self.calls.append('trust'); self.has_trust = True
        if self.on_trust: self.on_trust()
        if self.failure == 'cancel': raise Cancelled()
    def remove_trust(self, cert): self.calls.append('untrust'); self.has_trust = False
    def register(self, manifest, previous=None):
        super().register(manifest, previous)
        if self.failure == 'partial': raise OSError('Synthetic partial write')
    def open_word(self, path): self.calls.append('open')


class WordUpgradeTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def environments(self):
        for platform in (('win32', 'darwin') if sys.platform == 'win32' else ('darwin',)):
            root = self.base/platform; root.mkdir()
            settings = IsolatedSettings(root, platform)
            if platform == 'win32':
                def cleanup(settings=settings):
                    import winreg
                    try: winreg.DeleteKey(winreg.HKEY_CURRENT_USER, settings.registry)
                    except FileNotFoundError: pass
                self.addCleanup(cleanup)
            old = root/'old'; old.mkdir()
            manifest = word_setup.manifest(old, 9443)
            settings.register(manifest)
            before = settings.registration_snapshot()
            data = root/'new'; data.mkdir()
            app = FastAPI(); app.state.word_panels = WordPanels()
            installer = Installer(data, app, settings); app.state.word_installer = installer
            yield settings, installer, before, manifest

    def install(self, installer, token=None):
        # The migration tests exercise native storage, not OS trust or Word.
        with patch('exhibit.word_tls.start_optional', return_value=True), patch.object(word_setup, 'status', return_value={'state':'trusted'}):
            installer._install(token)
        return installer.status()

    def test_detect_before_preparation_then_upgrade_restart_remove_and_reinstall(self):
        for settings, installer, before, manifest in self.environments():
            original_file = manifest.read_bytes()
            status = installer.status()
            self.assertEqual(status['phase'], 'needs_update')
            self.assertFalse(status['installed'])
            self.assertFalse((installer.root/'word-connection.json').exists())
            self.assertEqual(settings.calls, [])
            report = json.dumps(diagnostics.report({'word':{'state':'not_configured', 'installation':status}}))
            self.assertNotIn(str(manifest), report)
            self.assertNotIn(status['replacement_token'], report)
            self.assertEqual(self.install(installer)['phase'], 'error')  # Consent to a fresh install isn't consent to replace.
            self.assertEqual(settings.registration_snapshot(), before)
            self.assertEqual(settings.calls, [])
            self.assertEqual(self.install(installer, status['replacement_token'])['phase'], 'waiting_word')
            record = installer._record()
            self.assertEqual(record['previous_registration'], before)
            self.assertTrue(installer.status()['installed'])
            self.assertEqual(self.install(installer)['phase'], 'waiting_word')  # Retry must retain the original backup.
            self.assertEqual(installer._record()['previous_registration'], before)
            restarted = Installer(installer.root, installer.app, settings)
            self.assertTrue(restarted.status()['installed'])
            restarted._remove()
            self.assertEqual(settings.registration_snapshot(), before)
            self.assertFalse(settings.has_trust)
            self.assertEqual(manifest.read_bytes(), original_file)
            self.assertEqual(restarted.status()['phase'], 'removed')
            self.assertIn('replacement_token', restarted.status())
            self.assertEqual(self.install(restarted, restarted.status()['replacement_token'])['phase'], 'waiting_word')
            restarted._remove()

    def test_cancel_and_partial_write_restore_previous_registration_and_persist_failure(self):
        for settings, installer, before, manifest in self.environments():
            for failure in ('cancel', 'partial'):
                settings.failure = failure
                status = self.install(installer, installer.status()['replacement_token'])
                self.assertIn(status['phase'], ('cancelled', 'error'))
                self.assertFalse(settings.has_trust)
                self.assertEqual(settings.registration_snapshot(), before)
                self.assertNotIn('open', settings.calls)
                restarted = Installer(installer.root, installer.app, settings)
                self.assertEqual(restarted.status()['phase'], status['phase'])
                self.assertEqual(restarted.status()['message'], status['message'])
                installer = restarted; installer.app.state.word_installer = installer
            settings.failure = None
            self.assertTrue(self.install(installer, installer.status()['replacement_token'])['installed'])
            installer._remove()

    def test_changed_registration_after_consent_is_not_overwritten(self):
        for settings, installer, before, manifest in self.environments():
            token = installer.status()['replacement_token']
            third = manifest.parent.parent/'third'; third.mkdir()
            other = word_setup.manifest(third, 9553)
            settings.register(other, previous=before)
            changed = settings.registration_snapshot()
            self.assertEqual(self.install(installer, token)['phase'], 'error')
            self.assertEqual(settings.registration_snapshot(), changed)
            self.assertEqual(settings.calls, [])

    def test_changed_registration_during_prompt_is_preserved_including_remove(self):
        for settings, installer, before, manifest in self.environments():
            other_dir = manifest.parent.parent/'other'; other_dir.mkdir()
            other = word_setup.manifest(other_dir, 9553)
            settings.on_trust = lambda: settings.register(other, previous=before)
            result = self.install(installer, installer.status()['replacement_token'])
            changed = settings.registration_snapshot()
            self.assertEqual(result['phase'], 'error')
            self.assertFalse(settings.has_trust)
            self.assertTrue(settings.registration(other))
            installer._remove()
            self.assertEqual(settings.registration_snapshot(), changed)
            self.assertEqual(installer.status()['phase'], 'error')

    def test_unknown_registration_never_offers_replacement(self):
        for settings, installer, before, manifest in self.environments():
            unknown = manifest.parent/'unknown.xml'; unknown.write_bytes(b'<unrelated/>')
            settings.register(unknown, previous=before)
            snapshot = settings.registration_snapshot()
            self.assertTrue(installer.status()['registration_blocked'])
            self.assertNotIn('replacement_token', installer.status())
            self.assertEqual(self.install(installer, '0'*64)['phase'], 'error')
            self.assertEqual(settings.registration_snapshot(), snapshot)
            self.assertEqual(settings.calls, [])

    def test_failed_setup_restarted_with_https_does_not_suggest_opening_word(self):
        for settings, installer, before, manifest in self.environments():
            # Reproduce an old failed candidate: local files exist, ownership marker does not.
            word_setup.prepare(installer.root, 8765)
            listener = threading.Thread(target=lambda: None)
            installer.app.state.word_connection = {'configured':True}
            installer.app.state.word_listener = (None, listener)
            self.install(installer)
            restarted = Installer(installer.root, installer.app, settings)
            installer.app.state.word_installer = restarted
            with patch.object(listener, 'is_alive', return_value=True):
                result = word_setup.status(installer.root, installer.app)
            self.assertEqual(result['state'], 'running')
            self.assertEqual(result['installation']['phase'], 'error')
            self.assertEqual(result['panel']['state'], 'setup_required')
            self.assertFalse(result['installation']['installed'])
            self.assertIn('replacement_token', result['installation'])

    def test_interrupted_operation_survives_restart_and_does_not_claim_success(self):
        for settings, installer, before, manifest in self.environments():
            installer._phase('registering', 'Подключаем панель…')
            restarted = Installer(installer.root, installer.app, settings)
            self.assertEqual(restarted.status()['phase'], 'error')
            self.assertIn('прервалась', restarted.status()['message'])
            self.assertEqual(settings.registration_snapshot(), before)

    def test_removed_registration_invalidates_saved_installed_state(self):
        for settings, installer, before, manifest in self.environments():
            self.install(installer, installer.status()['replacement_token'])
            settings.unregister(installer.root/'BLExhibitManager.Word.xml')
            self.assertFalse(installer.status()['installed'])
            self.assertEqual(installer.status()['phase'], 'error')
            with self.assertRaises(ValueError): installer.open()
            installer._remove()
            self.assertEqual(settings.registration_snapshot(), before)
