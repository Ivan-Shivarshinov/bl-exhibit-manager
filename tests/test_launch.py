from unittest import TestCase
from unittest.mock import patch,MagicMock
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from exhibit import launch
from exhibit.app import app, translations
from exhibit import external_process


class LaunchTests(TestCase):
    def test_source_and_frozen_commands_are_distinct(self):
        with patch.object(launch.sys,'frozen',False,create=True):
            self.assertEqual(launch.command('--serve')[1:3],['-m','exhibit.launch'])
        with patch.object(launch.sys,'frozen',True,create=True):
            self.assertEqual(launch.command('--serve')[1:],['--serve'])

    def test_existing_instance_does_not_spawn_or_replace_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'web/dist').mkdir(parents=True);(root/'web/dist/index.html').write_text('UI')
            with patch.object(launch,'WORKSPACE',root),patch.object(launch,'health',return_value={'version':'0.1.0'}),patch.object(launch.subprocess,'Popen') as spawn:
                self.assertEqual(launch.launch(8765,True),'http://127.0.0.1:8765');spawn.assert_not_called()
            with patch.object(launch,'WORKSPACE',root),patch.object(launch,'health',return_value={'version':'older'}),self.assertRaisesRegex(ValueError,'другая версия'):
                launch.launch(8765,True)

    def test_shutdown_needs_local_header_and_waits_for_translation(self):
        client=TestClient(app,base_url='http://127.0.0.1');stop=MagicMock();previous=getattr(app.state,'stop_server',None);app.state.stop_server=stop
        try:
            self.assertEqual(client.post('/api/application/stop').status_code,403)
            with patch.object(translations,'active',{'test':True}):
                self.assertEqual(client.post('/api/application/stop',headers={'X-Exhibit-Local':'1'}).status_code,400)
                stop.assert_not_called()
            self.assertTrue(client.post('/api/application/stop',headers={'X-Exhibit-Local':'1'}).json()['stopping']);stop.assert_called_once()
        finally:app.state.stop_server=previous

    def test_frozen_external_tools_do_not_inherit_bundle_library_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            env={'PATH':str(Path(folder)/'bin')+'/not-a-separator', 'DYLD_LIBRARY_PATH':folder}
            with patch.object(external_process.sys,'frozen',True,create=True),patch.object(external_process.sys,'_MEIPASS',folder,create=True),patch.object(external_process.sys,'platform','darwin'),patch.object(external_process.subprocess,'Popen') as spawn:
                external_process.popen(['tool'],env=env)
                actual=spawn.call_args.kwargs['env'];self.assertNotIn('DYLD_LIBRARY_PATH',actual)
                self.assertNotIn(folder,actual['PATH']);self.assertIn('/opt/homebrew/bin',actual['PATH'])
