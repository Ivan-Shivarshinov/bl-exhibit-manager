from types import SimpleNamespace
from unittest import TestCase
import sys
from unittest.mock import patch, Mock
from exhibit import browser_handoff


class BrowserHandoffTests(TestCase):
    def test_native_http_browser_lookup_is_read_only(self):
        if sys.platform == 'win32': self.assertTrue(browser_handoff.windows_browser_class())
        elif sys.platform == 'darwin': self.assertTrue(browser_handoff.mac_browser_bundle())

    def test_windows_launch_uses_browser_class_and_retains_security_checks(self):
        fields = {}
        execute = Mock(side_effect=lambda pointer: fields.update(file=pointer._obj.file, browser=pointer._obj.class_name,
                        flags=pointer._obj.mask, parameters=pointer._obj.parameters) or True)
        with patch.object(browser_handoff.ctypes, 'WinDLL', create=True) as dll, patch.object(browser_handoff, 'windows_browser_class', return_value='HTTPBrowser'):
            dll.return_value.ShellExecuteExW = execute
            browser_handoff.windows_open('file:///private/browser.html')
        self.assertEqual(fields['browser'], 'HTTPBrowser'); self.assertEqual(fields['file'], 'file:///private/browser.html')
        self.assertIsNone(fields['parameters']); self.assertEqual(fields['flags'] & 0x00800000, 0)  # Never NOZONECHECKS.

    def test_windows_selects_http_handler_instead_of_html_file_association(self):
        controller = type('WindowsDefault', (), {'open': Mock(side_effect=AssertionError('File association is not a browser'))})()
        with patch.object(browser_handoff.sys, 'platform', 'win32'), patch.object(browser_handoff.webbrowser, 'get', return_value=controller), patch.object(browser_handoff, 'windows_open') as opened:
            browser_handoff.open_entry('file:///private/browser.html')
            opened.assert_called_once_with('file:///private/browser.html')

    def test_explicit_browser_controller_receives_only_private_file_uri(self):
        controller = SimpleNamespace(open=Mock(return_value=True), name='chosen-browser')
        with patch.object(browser_handoff.webbrowser, 'get', return_value=controller):
            browser_handoff.open_entry('file:///private/browser.html')
            controller.open.assert_called_once_with('file:///private/browser.html')
            with self.assertRaises(ValueError): browser_handoff.open_entry('http://localhost/#secret')

    def test_mac_http_bundle_gets_private_file_not_bootstrap_credential(self):
        controller = SimpleNamespace(name='default')
        with patch.object(browser_handoff.sys, 'platform', 'darwin'), patch.object(browser_handoff.webbrowser, 'get', return_value=controller), patch.object(browser_handoff, 'mac_browser_bundle', return_value='org.example.browser'), patch('exhibit.external_process.popen') as opened:
            opened.return_value.wait.return_value = 0
            browser_handoff.open_entry('file:///private/browser.html')
            self.assertEqual(opened.call_args.args[0], ['/usr/bin/open', '-b', 'org.example.browser', 'file:///private/browser.html'])
