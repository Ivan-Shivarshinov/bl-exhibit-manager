"""Disposable CI only: native registration/trust; explicitly simulated Word.

The real Word host and its system dialogs are not simulated as evidence.
"""
import json
import os
from pathlib import Path
import secrets
import sys
from tempfile import TemporaryDirectory
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi import FastAPI
from docx import Document
from exhibit.word_connection import WordPanels
from exhibit.word_install import Installer, NativeSettings, ADDIN_ID
from exhibit import word_setup


class SimulatedWord(NativeSettings):
    def check_word(self): return Path('Simulated Word host')
    def open_word(self, path):
        assert 'учебный' in Document(path).paragraphs[1].text
        self.opened = getattr(self, 'opened', 0) + 1


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform not in ('win32','darwin'):
        raise SystemExit('Run only on disposable GitHub Actions Windows/macOS runners.')
    with TemporaryDirectory(prefix='bl-word-native-') as directory:
        root = Path(directory)
        registration = r'Software\BLExhibitManager\CI\' + uuid.uuid4().hex
        settings = SimulatedWord(home=root/'home', registry=registration, keychain=root/'CI.keychain-db', admin_trust=sys.platform=='darwin')
        settings.home.mkdir()
        if sys.platform == 'darwin':
            password = secrets.token_urlsafe(24)
            settings.run(['/usr/bin/security','create-keychain','-p',password,str(settings.keychain)])
            settings.run(['/usr/bin/security','unlock-keychain','-p',password,str(settings.keychain)])
            foreign = settings._mac_target().parent / 'unrelated.xml'
            foreign.parent.mkdir(parents=True, exist_ok=True); foreign.write_bytes(b'Unrelated synthetic add-in')
        else:
            import winreg
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, registration) as key:
                winreg.SetValueEx(key, 'Unrelated', 0, winreg.REG_SZ, 'Preserve this synthetic value')
        app = FastAPI(); app.state.http_port = 8765; app.state.word_panels = WordPanels()
        @app.get('/api/health')
        def health(): return {'application':'bl-exhibit-manager'}
        data = root/'data'; installer = Installer(data, app, settings); app.state.word_installer = installer
        def complete(remove=False):
            installer.begin(remove); installer.thread.join(60)
            assert not installer.thread.is_alive(), 'Native setup did not finish'
            result=installer.status()
            assert result['phase'] == ('removed' if remove else 'waiting_word'), result
        try:
            complete()
            config, _ = word_setup.configuration(data)
            certificate = Path(config['cert'])
            assert settings.trusted(certificate)
            assert settings.registration(data/'BLExhibitManager.Word.xml')
            assert word_setup.status(data, app, True)['state'] == 'trusted'
            snapshot = {str(p.relative_to(data)):p.read_bytes() for p in data.rglob('*') if p.is_file() and p.suffix in ('.crt','.key','.xml')}
            complete()
            assert settings.opened == 2
            assert snapshot == {name:(data/name).read_bytes() for name in snapshot}
            complete(remove=True)
            assert not settings.trusted(certificate)
            assert not settings.registration(data/'BLExhibitManager.Word.xml')
            assert snapshot == {name:(data/name).read_bytes() for name in snapshot}
            complete(); complete(remove=True)
            if sys.platform == 'darwin': assert foreign.read_bytes() == b'Unrelated synthetic add-in'
            else:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registration) as key:
                    assert winreg.QueryValueEx(key,'Unrelated')[0] == 'Preserve this synthetic value'
            print(json.dumps({'platform':sys.platform,'result':'passed','checks':['native registration','native system TLS trust','repeat','remove','reinstall','retained certificate/key','unrelated settings preserved','new synthetic DOCX'], 'word_host':'simulated', 'mac_trust_domain':'admin on disposable runner' if sys.platform=='darwin' else None}))
        finally:
            installer.cancel.set()
            if installer.thread: installer.thread.join(5)
            # Remove only this run's certificate and this isolated registration.
            if (data/'word-connection.json').exists():
                config, _ = word_setup.configuration(data)
                settings.remove_trust(Path(config['cert']))
                settings.unregister(data/'BLExhibitManager.Word.xml')
            listener = getattr(app.state,'word_listener',None)
            if listener: listener[0].should_exit=True; listener[1].join(5)
            if sys.platform=='darwin': settings.run(['/usr/bin/security','delete-keychain',str(settings.keychain)])
            else:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registration, 0, winreg.KEY_SET_VALUE) as key: winreg.DeleteValue(key,'Unrelated')
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, registration)


if __name__=='__main__': main()
