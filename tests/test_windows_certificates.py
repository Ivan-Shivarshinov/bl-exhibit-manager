from pathlib import Path
import os
import ssl
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless
import uuid

from exhibit import word_setup, windows_certificates
from exhibit.word_install import NativeSettings


@skipUnless(sys.platform == 'win32', 'Windows system-store API')
class WindowsCertificatesTests(TestCase):
    def test_exact_deletion_repeat_and_reinstall_preserve_other_roots_and_same_name_certificate(self):
        import winreg
        with TemporaryDirectory() as folder:
            base=Path(folder); store='BLExhibit-test-'+uuid.uuid4().hex
            before=ssl.enum_certificates('ROOT')
            tool=str(Path(os.environ['SYSTEMROOT'])/'System32/certutil.exe')
            settings=NativeSettings()
            for name in ('one','two'):word_setup.prepare(base/name,8765)
            paths=[Path(word_setup.configuration(base/name)[0]['cert']) for name in ('one','two')]
            ders=[word_setup.configuration(base/name)[1].public_bytes(word_setup.serialization.Encoding.DER) for name in ('one','two')]
            try:
                for path in paths:settings.run([tool,'-user','-f','-addstore',store,str(path)],timeout=10)
                self.assertEqual({c[0] for c in ssl.enum_certificates(store)},set(ders))
                self.assertTrue(windows_certificates.remove(ders[0],store=store))
                self.assertEqual({c[0] for c in ssl.enum_certificates(store)},{ders[1]})
                self.assertFalse(windows_certificates.remove(ders[0],store=store))
                settings.run([tool,'-user','-f','-addstore',store,str(paths[0])],timeout=10)
                self.assertTrue(windows_certificates.remove(ders[0],store=store))
                self.assertEqual({c[0] for c in ssl.enum_certificates('ROOT')},{c[0] for c in before})
            finally:
                for der in ders:windows_certificates.remove(der,store=store)
                prefix='Software\\Microsoft\\SystemCertificates\\'+store
                # Only the unique non-trusting store created above is removed.
                def cleanup(path):
                    try:
                        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,path) as key:
                            children=[winreg.EnumKey(key,i) for i in range(winreg.QueryInfoKey(key)[0])]
                        for child in children:cleanup(path+'\\'+child)
                        winreg.DeleteKey(winreg.HKEY_CURRENT_USER,path)
                    except FileNotFoundError:pass
                cleanup(prefix)
