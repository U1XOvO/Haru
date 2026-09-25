"""Speech IPC checks with a fake provider and a real disposable subprocess."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]

# Supply the shared engine at its public boundary: no online provider is loaded,
# and any accidental learning service import makes the request fail immediately.
HARNESS = r'''
import asyncio
import importlib.abc
import os
from pathlib import Path
import sys
import types

sys.path.insert(0, os.environ['HARU_TEST_BACKEND'])
class NoService(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'service':
            raise AssertionError('Speech IPC must not import the learning service')
sys.meta_path.insert(0, NoService())
speech = types.ModuleType('speech')
class SpeechError(Exception): pass
class SpeechEngine:
    def __init__(self, data_dir): self.root = Path(data_dir)
    async def prepare(self, params, cancelled):
        behavior = os.environ.get('HARU_TEST_BEHAVIOR', 'success')
        if behavior == 'error':
            raise SpeechError('在线朗读暂时不可用，请稍后重试。')
        if behavior == 'unexpected':
            raise RuntimeError('private-provider-diagnostic')
        folder = self.root / 'tts-cache'
        folder.mkdir(parents=True, exist_ok=True)
        if behavior == 'cancel':
            pending = folder / ('a'*32 + '.pending.mp3')
            pending.write_bytes(b'incomplete')
            (self.root / 'ready').write_text('ready')
            try:
                await asyncio.Event().wait()
            finally:
                pending.unlink(missing_ok=True)
        path = folder / ('a'*32 + '.mp3')
        if behavior == 'outside': path = self.root / ('a'*32 + '.mp3')
        if behavior == 'badname': path = folder / 'caller-chosen.mp3'
        path.write_bytes(b'MP3:offline-test')
        if behavior == 'symlink':
            outside = self.root / 'outside.mp3'
            outside.write_bytes(b'MP3:outside')
            path.unlink()
            path.symlink_to(outside)
        if behavior == 'missing': path.unlink()
        return types.SimpleNamespace(path=path, transient=params.get('cache') is False)
speech.SpeechEngine = SpeechEngine
speech.SpeechError = SpeechError
sys.modules['speech'] = speech
import bridge
bridge.main()
'''


class SpeechBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='haru-speech-ipc-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def environment(self, behavior='success'):
        return dict(os.environ, HARU_TEST_BACKEND=str(ROOT / 'backend'),
                    HARU_TEST_BEHAVIOR=behavior, HARU_DATA_DIR=str(self.root),
                    HARU_STORAGE_DIR=str(self.root),
                    PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')

    def request(self, params=None, behavior='success'):
        payload = {'action': 'speech_prepare', 'params': {'text': 'こんにちは'} if params is None else params}
        result = subprocess.run([sys.executable, '-c', HARNESS], input=json.dumps(payload),
                                text=True, encoding='utf-8', capture_output=True,
                                env=self.environment(behavior), timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertFalse((self.root / 'haru.sqlite3').exists())
        self.assertFalse((self.root / '.schema.lock').exists())
        return json.loads(result.stdout)

    def test_speech_prepare_does_not_import_service_or_initialize_database(self):
        result = self.request()
        self.assertEqual(result, {'ok': True, 'data': {
            'audio': 'a'*32+'.mp3', 'transient': False, 'engine': 'edge', 'fallback': ''}})
        self.assertTrue((self.root / 'tts-cache' / result['data']['audio']).is_file())

    def test_transient_result_is_left_for_native_playback_to_consume(self):
        result = self.request({'text': '秘密の練習', 'cache': False})
        self.assertEqual(result['data']['transient'], True)
        self.assertTrue((self.root / 'tts-cache' / result['data']['audio']).is_file())

    def test_expected_provider_error_is_friendly_and_unexpected_error_is_sanitized(self):
        self.assertEqual(self.request(behavior='error'),
                         {'ok': False, 'error': '在线朗读暂时不可用，请稍后重试。'})
        result = self.request(behavior='unexpected')
        self.assertFalse(result['ok'])
        self.assertNotIn('private-provider-diagnostic', result['error'])

    def test_invalid_params_and_audio_paths_are_rejected(self):
        self.assertFalse(self.request(params=['invalid'])['ok'])
        for behavior in ('outside', 'badname', 'missing', 'symlink'):
            with self.subTest(behavior=behavior):
                if behavior == 'symlink':
                    with tempfile.TemporaryDirectory(dir=self.root) as temporary:
                        target = Path(temporary) / 'target'
                        target.write_bytes(b'probe')
                        try:
                            (Path(temporary) / 'link').symlink_to(target)
                        except (OSError, NotImplementedError) as error:
                            self.skipTest('File symlink creation unavailable: ' + type(error).__name__)
                result = self.request(behavior=behavior)
                self.assertFalse(result['ok'])
                self.assertEqual(result['error'], '朗读音频文件无效，请重新播放。')

    @unittest.skipIf(sys.platform == 'win32', 'TerminateProcess does not deliver POSIX SIGTERM')
    def test_sigterm_cancels_synthesis_and_cleans_its_pending_file(self):
        process = subprocess.Popen([sys.executable, '-c', HARNESS], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding='utf-8', env=self.environment('cancel'))
        try:
            process.stdin.write(json.dumps({'action': 'speech_prepare', 'params': {'text': '中止'}}))
            process.stdin.close()
            process.stdin = None
            deadline = time.monotonic() + 3
            while not (self.root / 'ready').exists() and time.monotonic() < deadline:
                if process.poll() is not None:
                    self.fail('Speech worker exited before the fake provider started')
                time.sleep(.01)
            self.assertTrue((self.root / 'ready').is_file())
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=3)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(stderr, '')
            self.assertEqual(json.loads(stdout), {'ok': True, 'data': {'cancelled': True}})
            self.assertEqual(list((self.root / 'tts-cache').glob('*.pending.*')), [])
            self.assertFalse((self.root / 'haru.sqlite3').exists())
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()


if __name__ == '__main__': unittest.main()
