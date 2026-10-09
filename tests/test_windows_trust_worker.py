import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import subprocess
import sys
from unittest import TestCase
from unittest.mock import patch

from exhibit import launch, windows_certificates, word_setup
from exhibit.word_install import Cancelled, NativeSettings


class TrustWorkerTests(TestCase):
    def test_cancel_and_timeout_terminate_the_private_child(self):
        for cancelled in (True,False):
            with self.subTest(cancelled=cancelled):
                child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                event=threading.Event()
                if cancelled: event.set()
                try:
                    with patch('exhibit.word_install.popen',return_value=child):
                        with self.assertRaises(Cancelled if cancelled else ValueError):
                            NativeSettings().run(['private-child'],event,timeout=.05)
                    self.assertIsNotNone(child.poll())
                finally:
                    if child.poll() is None: child.kill(); child.communicate()

    def test_error_result_has_native_code_without_private_file_paths(self):
        with TemporaryDirectory(prefix='PRIVATE-PATH-') as folder:
            root=Path(folder); word_setup.prepare(root,8765)
            result=root/'result.json'
            with patch.object(windows_certificates,'add',side_effect=windows_certificates.CertificateError('добавить сертификат',1223)):
                code=windows_certificates.add_worker(word_setup.configuration(root)[0]['cert'],result)
            data=json.loads(result.read_text())
            self.assertEqual(code,1); self.assertEqual(data['code'],1223)
            self.assertNotIn('PRIVATE-PATH',result.read_text())
            self.assertNotIn('PRIVATE KEY',result.read_text())

    def test_worker_mode_does_not_launch_http_app_or_browser(self):
        with patch.object(launch.sys,'argv',['app','--windows-root-add','cert','result']), patch.object(launch.sys,'platform','win32'), patch.object(windows_certificates,'add_worker',return_value=0) as worker, patch.object(launch,'launch') as app:
            self.assertEqual(launch.main(),0)
            worker.assert_called_once_with('cert','result',admin=False,store='Root')
            app.assert_not_called()

    def test_native_failure_is_reported_by_installer_without_certutil(self):
        settings=NativeSettings(platform='win32')
        def helper(args,cancel,**kwargs):
            position=args.index('--windows-root-add')
            Path(args[position+2]).write_text(json.dumps({'error':'Windows отказала (код 0x000004c7).','code':1223}))
            self.assertNotIn('certutil',str(args))
            self.assertNotIn('--windows-root-machine',args)
            return 1,b''
        with patch.object(settings,'run',side_effect=helper):
            with self.assertRaisesRegex(ValueError,'0x000004c7'):
                settings.install_trust(Path('synthetic.crt'),threading.Event())
