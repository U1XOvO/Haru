"""Offline desktop contracts, real subprocesses and isolated storage."""
import concurrent.futures
import json
from pathlib import Path
import re
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'native'), str(ROOT / 'scripts')]
import start
from desktop_bridge import BACKEND_ACTIONS, Backend, DesktopError
from init_env import initialize
import windows_app
import windows_audio


class DesktopTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='haru desktop 日本語 ')
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)
        self.events = []
        self.backend = Backend(self.path, lambda i, e: self.events.append((i, e)))
        self.addCleanup(self.backend.close)

    def request(self, action, params=None):
        return self.backend.request({'id': 1, 'action': action, 'params': params or {}})

    def test_real_backend_unicode_and_persistence(self):
        result = self.request('card_seed')
        self.assertTrue(result['ok'])
        self.assertTrue(any('word' in card for card in result['data']))
        self.assertEqual(result['data'], self.request('cards')['data'])
        self.assertNotIn('\\u', json.dumps(result['data'], ensure_ascii=False))
        self.assertTrue((self.path / 'haru.sqlite3').is_file())
        exported = self.request('export', {'type': 'json'})
        self.assertTrue(Path(exported['data']['path']).is_file())

    def test_unknown_large_or_malformed_requests_never_spawn(self):
        with patch('desktop_bridge.subprocess.Popen') as spawn:
            for message in [None, {}, {'id': True, 'action': 'cards'},
                            {'id': 1, 'action': '__dict__'},
                            {'id': 1, 'action': 'cards', 'params': []},
                            {'id': 1, 'action': 'cards', 'params': {'word': '日' * 40_000}}]:
                with self.assertRaises(DesktopError): self.backend.request(message)
            spawn.assert_not_called()

    def test_mac_and_windows_backend_allowlists_match(self):
        swift = (ROOT / 'native/Haru.swift').read_text(encoding='utf-8')
        actions = re.search(r'let actions:Set<String>=\[(.*?)\]', swift)[1]
        self.assertEqual(BACKEND_ACTIONS, set(re.findall(r'"([a-z_]+)"', actions)))

    def fixture_bridge(self, contents):
        self.backend.bridge = self.path / 'fixture.py'
        self.backend.bridge.write_text(contents, encoding='utf-8')

    def test_stream_events_and_result(self):
        self.fixture_bridge('import json,sys\nsys.stdin.buffer.read()\n'
                            'print(json.dumps({"type":"delta","text":"こんにちは"}),flush=True)\n'
                            'print(json.dumps({"ok":True,"data":{"jp":"日本語"}}))\n')
        result = self.request('chat_stream', {'request_id': 'fixture-stream-1234'})
        self.assertEqual(self.events, [(1, {'type': 'delta', 'text': 'こんにちは'})])
        self.assertEqual(result['data']['jp'], '日本語')
        self.assertFalse(self.backend.processes)

    def test_timeout_reaps_child(self):
        self.fixture_bridge('import sys,time\nsys.stdin.buffer.read()\ntime.sleep(20)\n')
        self.backend.deadline = .2
        with self.assertRaisesRegex(DesktopError, '上限'): self.request('cards')
        self.assertFalse(self.backend.processes)

    def test_cancel_and_shutdown_reap_active_streams(self):
        self.fixture_bridge('import json,sys,time\nr=json.load(sys.stdin)\n'
                            'if r["action"]=="chat_stream":\n'
                            ' print(json.dumps({"type":"delta","text":"はい"}),flush=True)\n'
                            ' time.sleep(20)\n'
                            'else: print(json.dumps({"ok":True,"data":{"status":"cancelled"}}))\n')
        for shutdown in (False, True):
            self.events.clear()
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(self.request, 'chat_stream', {'request_id': 'fixture-cancel-123'})
                end = time.monotonic() + 5
                while not self.events and time.monotonic() < end: time.sleep(.01)
                self.assertTrue(self.events)
                if shutdown: self.backend.close()
                else: self.assertTrue(self.request('chat_cancel', {'request_id': 'fixture-cancel-123'})['ok'])
                with self.assertRaises(DesktopError): future.result(timeout=5)
            self.assertFalse(self.backend.processes)
        with self.assertRaises(DesktopError): self.request('cards')

    def test_large_and_invalid_output_reaps_child(self):
        for body in ['print("x"*2000001)', 'print("not json")']:
            self.fixture_bridge('import sys\nsys.stdin.buffer.read()\n' + body)
            with self.assertRaises((DesktopError, ValueError)): self.request('cards')
            self.assertFalse(self.backend.processes)

    def test_import_limits_unicode_and_asset_path(self):
        paper = self.path / '試験.json'
        paper.write_text('\ufeff' + json.dumps({'title': '日语'}), encoding='utf-8')
        self.assertEqual(windows_app.import_file(self.path, paper)['paper']['title'], '日语')
        media = self.path / '題図.jpeg'
        media.write_bytes(b'fixture-image')
        item = windows_app.import_file(self.path, media)
        self.assertTrue(item['asset'].endswith('.jpg'))
        self.assertEqual(windows_app.asset_path(self.path, item['asset']).read_bytes(), media.read_bytes())
        for identity in ['../private.pdf', 'a'*32 + '.exe', 'a'*32 + '.pdf']:
            with self.assertRaises(DesktopError): windows_app.asset_path(self.path, identity)
        paper.write_bytes(b' ' * 95_001)
        with self.assertRaisesRegex(DesktopError, '95KB'): windows_app.import_file(self.path, paper)

    def test_only_local_ui_can_call_host(self):
        host = windows_app.Host(self.path)
        self.addCleanup(host._close)
        host.window = Mock()
        for url in ['https://example.com', (ROOT / '.env').as_uri(), 'file://remote/share/index.html']:
            host.window.get_current_url.return_value = url
            with patch.object(host.backend, 'request') as request:
                self.assertFalse(host._request({'id': 1, 'action': 'cards'})['ok'])
                request.assert_not_called()
        self.assertTrue(windows_app.is_app_url((ROOT / 'ui/index.html').as_uri() + '#home'))

    def test_legacy_renderer_is_rejected(self):
        host = windows_app.Host(self.path)
        self.addCleanup(host._close)
        with patch('sys.stderr'):
            self.assertFalse(host._initialized('mshtml'))
        self.assertFalse(host.renderer_ready)
        host._initialized('edgechromium')
        self.assertTrue(host.renderer_ready)

    def test_external_links_and_reveal_are_restricted(self):
        host = windows_app.Host(self.path)
        self.addCleanup(host._close)
        host.window = Mock()
        host.window.get_current_url.return_value = (ROOT / 'ui/index.html').as_uri()
        with patch('windows_app.webbrowser.open') as opened:
            for url in ['http://www.jlpt.jp', 'https://www.jlpt.jp.evil.example',
                        'https://user:pass@www.jlpt.jp', 'file:///etc/passwd']:
                self.assertFalse(host._request({'id': 1, 'action': 'open_url', 'params': {'url': url}})['ok'])
            opened.assert_not_called()
            self.assertTrue(host._request({'id': 1, 'action': 'open_url', 'params': {'url': 'https://www.jlpt.jp/'}})['ok'])
            opened.assert_called_once()
        with patch('windows_app.subprocess.Popen') as spawn:
            self.assertFalse(host._request({'id': 1, 'action': 'reveal', 'params': {'path': str(ROOT / '.env')}})['ok'])
            spawn.assert_not_called()

    def test_launch_dispatch_and_unsupported_platform(self):
        with patch('start.subprocess.call', return_value=0) as call:
            self.assertEqual(start.launch('Darwin'), 0)
            self.assertEqual(call.call_args.args[0], ['bash', str(ROOT / 'start.command')])
            self.assertEqual(start.launch('Windows'), 0)
            self.assertIn(str(ROOT / 'scripts/setup_windows.ps1'), call.call_args.args[0])
            call.reset_mock()
            self.assertEqual(start.launch('Linux'), 1)
            call.assert_not_called()

    def test_init_preserves_existing_configuration(self):
        (self.path / '.env.example').write_text('example', encoding='utf-8')
        initialize(self.path)
        self.assertEqual((self.path / '.env').read_text(), 'example')
        (self.path / '.env').write_text('existing settings', encoding='utf-8')
        initialize(self.path)
        self.assertEqual((self.path / '.env').read_text(), 'existing settings')

    def test_windows_ui_copy_does_not_change_mac_csp(self):
        (self.path / 'ui').mkdir()
        html = (ROOT / 'ui/index.html').read_text(encoding='utf-8')
        (self.path / 'ui/index.html').write_text(html, encoding='utf-8')
        with patch.object(windows_app, 'ROOT', self.path):
            index = windows_app.prepare_ui()
        self.assertIn("script-src 'self' 'unsafe-eval'", index.read_text(encoding='utf-8'))
        self.assertEqual((self.path / 'ui/index.html').read_text(encoding='utf-8'), html)

    def test_recording_closes_device_before_atomic_save_and_cleanup(self):
        commands = []
        def mci(command, *args):
            commands.append(command)
            if command.startswith('save haru_record '):
                (self.path / 'speaking-latest.pending.wav').write_bytes(b'fixture-wav')
            return 0
        library = Mock()
        library.mciSendStringW.side_effect = mci
        with patch.object(windows_audio.ctypes, 'WinDLL', return_value=library, create=True):
            audio = windows_audio.WindowsAudio(self.path, Mock())
        self.addCleanup(audio.close)
        audio.perform('record_start', {})
        with self.assertRaisesRegex(DesktopError, '已经'): audio.perform('record_start', {})
        with self.assertRaisesRegex(DesktopError, '停止'): audio.perform('record_play', {})
        replace = Path.replace
        def checked_replace(source, destination):
            self.assertEqual(commands[-1], 'close haru_record')
            return replace(source, destination)
        with patch.object(Path, 'replace', checked_replace):
            audio.perform('record_stop', {})
        self.assertEqual(audio.path.read_bytes(), b'fixture-wav')
        self.assertFalse(audio.recording)
        audio.close()
        with self.assertRaisesRegex(DesktopError, '退出'): audio.perform('record_start', {})
        with self.assertRaisesRegex(DesktopError, '退出'): audio.play(audio.path)


if __name__ == '__main__': unittest.main()
