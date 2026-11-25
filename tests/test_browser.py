import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from portal_browser import launch_browser, system_candidates


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.chrome = self.root / 'chrome.exe'
        self.edge = self.root / 'msedge.exe'
        self.chrome.touch(); self.edge.touch()
        self.managed = self.root / 'managed.exe'
        self.engine = Mock(executable_path=str(self.managed))
        self.engine.launch.return_value.version = '123.0'
        self.log = Mock()

    def test_windows_standard_and_registry_paths(self):
        env = {'ProgramFiles': str(self.root / 'Program Files'), 'LOCALAPPDATA': str(self.root / 'Local')}
        with patch('portal_browser.windows_registry_paths', side_effect=lambda exe: [str(self.chrome)] if exe == 'chrome.exe' else []):
            items = list(system_candidates('win32', env, lambda exe: None))
        self.assertEqual(items[0], ('Google Chrome', self.chrome))
        self.assertIn(('Google Chrome', Path(env['LOCALAPPDATA']) / 'Google/Chrome/Application/chrome.exe'), items)
        self.assertIn(('Microsoft Edge', Path(env['ProgramFiles']) / 'Microsoft/Edge/Application/msedge.exe'), items)

    def test_auto_falls_back_after_failed_launch(self):
        self.engine.launch.side_effect = [RuntimeError('Chrome blocked'), Mock(version='124.0')]
        with patch('portal_browser.system_candidates', return_value=[('Google Chrome', self.chrome), ('Microsoft Edge', self.edge)]):
            _, info = launch_browser(self.engine, log=self.log)
        self.assertEqual(info['name'], 'Microsoft Edge')
        self.assertEqual(info['selection'], 'auto')
        self.assertEqual(self.engine.launch.call_count, 2)

    def test_managed_has_precedence(self):
        self.managed.touch()
        with patch('portal_browser.system_candidates', return_value=[('Google Chrome', self.chrome)]):
            _, info = launch_browser(self.engine, log=self.log)
        self.assertEqual(info['name'], 'Chromium Playwright')
        self.engine.launch.assert_called_once_with(headless=True)

    def test_managed_shell_missing_uses_full_executable(self):
        self.managed.touch()
        self.engine.launch.side_effect = [RuntimeError('shell missing'), Mock(version='124')]
        with patch('portal_browser.system_candidates', return_value=[]):
            _, info = launch_browser(self.engine, log=self.log)
        self.assertEqual(info['name'], 'Chromium Playwright')
        self.engine.launch.assert_called_with(executable_path=str(self.managed), headless=True)

    def test_explicit_override(self):
        self.managed.touch()
        _, info = launch_browser(self.engine, self.edge, log=self.log)
        self.assertEqual(info['selection'], 'explicit')
        self.engine.launch.assert_called_once_with(executable_path=str(self.edge), headless=True)

    def test_explicit_failure_no_fallback(self):
        self.engine.launch.side_effect = RuntimeError('blocked')
        with patch('portal_browser.system_candidates', return_value=[('Google Chrome', self.chrome)]):
            with self.assertRaisesRegex(RuntimeError, 'blocked'):
                launch_browser(self.engine, self.edge, log=self.log)
        self.assertEqual(self.engine.launch.call_count, 1)

    def test_missing_browser(self):
        with patch('portal_browser.system_candidates', return_value=[]):
            with self.assertRaisesRegex(RuntimeError, 'playwright install chromium'):
                launch_browser(self.engine, log=self.log)
        self.engine.launch.assert_not_called()

    def test_missing_explicit_path(self):
        with self.assertRaisesRegex(RuntimeError, 'non trovato'):
            launch_browser(self.engine, self.root / 'missing.exe', log=self.log)
        self.engine.launch.assert_not_called()

    def test_deduplicate_candidates(self):
        self.engine.launch.side_effect = RuntimeError('blocked')
        with patch('portal_browser.system_candidates', return_value=[('Chrome', self.chrome), ('Chrome duplicate', self.chrome)]):
            with self.assertRaises(RuntimeError):
                launch_browser(self.engine, log=self.log)
        self.engine.launch.assert_called_once()
