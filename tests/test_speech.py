"""Offline shared Edge TTS tests, with real local cache locks and a fake provider."""
import asyncio
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
provider = types.ModuleType('edge_tts')
provider.__version__ = 'test-1'
spec = importlib.util.spec_from_file_location('haru_speech_tested', ROOT / 'backend/speech.py')
speech = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = speech
with patch.dict(sys.modules, {'edge_tts': provider}):
    spec.loader.exec_module(speech)


class SpeechTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='haru-shared-speech-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.calls = []
        self.behavior = self.generate
        patcher = patch.object(provider, '__version__', 'test-1')
        patcher.start(); self.addCleanup(patcher.stop)
        owner = self

        class FakeCommunicate:
            def __init__(self, *, text, voice, rate):
                self.params = (text, voice, rate)

            async def save(self, output):
                owner.calls.append(self.params)
                await owner.behavior(*self.params, Path(output))

        patcher = patch.object(provider, 'Communicate', FakeCommunicate, create=True)
        patcher.start(); self.addCleanup(patcher.stop)
        self.engine = speech.SpeechEngine(self.root)

    async def generate(self, text, voice, rate, output):
        output.write_bytes(('MP3:' + text).encode())

    def prepare(self, text='こんにちは', **params):
        return asyncio.run(self.engine.prepare(dict(text=text, **params)))

    def cache(self):
        return json.loads((self.root / 'tts-cache/index.json').read_text(encoding='utf-8'))

    def assert_no_pending(self):
        self.assertEqual(list((self.root / 'tts-cache').glob('*.pending.*')), [])

    def test_normalization_is_shared_and_rejects_invalid_input(self):
        self.assertEqual(speech.normalize({'text': '雨'}), ('雨', 'ja-JP-NanamiNeural', '-16%'))
        self.assertEqual(speech.normalize({'text': '雨', 'rate': 0.1})[2], '-50%')
        self.assertEqual(speech.normalize({'text': '雨', 'rate': 1.5})[2], '+100%')
        for params in [{}, {'text': ' '}, {'text': 'a' * 6001}, {'text': '雨', 'voice': 'en-US-TestNeural'},
                       {'text': '雨', 'rate': float('nan')}, {'text': '雨', 'rate': 'bad'}]:
            with self.subTest(params=str(params)[:50]), self.assertRaises(speech.SpeechError):
                speech.normalize(params)

    def test_repeated_playback_and_restart_reuse_complete_audio(self):
        first = self.prepare()
        self.assertFalse(first.transient)
        self.assertEqual(self.prepare().path, first.path)
        self.engine = speech.SpeechEngine(self.root)
        self.assertEqual(self.prepare().path, first.path)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.cache()), 1)
        self.assert_no_pending()

    def test_voice_rate_and_engine_are_separate_cache_keys(self):
        self.prepare('雨')
        self.prepare('雨', voice='ja-JP-KeitaNeural')
        self.prepare('雨', rate=0.7)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(len(self.cache()), 3)
        with patch.object(provider, '__version__', 'test-2'):
            self.engine = speech.SpeechEngine(self.root)
        self.prepare('雨')
        self.assertEqual(len(self.calls), 4)
        self.assertEqual({item['key'][3] for item in self.cache()}, {'edge-tts:test-2'})

    def test_restart_rejects_truncated_audio(self):
        first = self.prepare('再生成')
        first.path.write_bytes(b'MP3:')
        self.engine = speech.SpeechEngine(self.root)
        second = self.prepare('再生成')
        self.assertNotEqual(first.path, second.path)
        self.assertFalse(first.path.exists())
        self.assertEqual(second.path.read_bytes(), 'MP3:再生成'.encode())
        self.assertEqual(len(self.calls), 2)

    def test_errors_empty_audio_and_timeout_leave_no_valid_cache(self):
        async def failed(text, voice, rate, output):
            output.write_bytes(b'partial')
            raise RuntimeError('fake network failure')

        async def empty(text, voice, rate, output):
            output.touch()

        async def stalled(text, voice, rate, output):
            output.write_bytes(b'partial')
            await asyncio.Event().wait()

        self.engine.synthesis_timeout = 0.01
        for behavior in [failed, empty, stalled]:
            with self.subTest(behavior=behavior.__name__):
                self.behavior = behavior
                with self.assertRaises(speech.SpeechError):
                    self.prepare('失敗')
                self.assertEqual(self.cache(), [])
                self.assertEqual(list((self.root / 'tts-cache').glob('*.mp3')), [])
                self.assert_no_pending()

    def test_cancelled_task_removes_partial_audio(self):
        async def scenario():
            started = asyncio.Event()

            async def stalled(text, voice, rate, output):
                output.write_bytes(b'partial')
                started.set()
                await asyncio.Event().wait()

            self.behavior = stalled
            task = asyncio.create_task(self.engine.prepare({'text': '途中'}))
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(scenario())
        self.assertEqual(self.cache(), [])
        self.assert_no_pending()

    def test_cancelled_callback_prevents_publication_and_rolls_back_committed_index(self):
        cancelled = threading.Event()

        async def finish_cancelled(text, voice, rate, output):
            output.write_bytes(b'complete')
            cancelled.set()

        self.behavior = finish_cancelled
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(self.engine.prepare({'text': '中断'}, cancelled=cancelled.is_set))
        self.assertEqual(self.cache(), [])
        self.assert_no_pending()
        cancelled.clear()
        self.behavior = self.generate
        original = self.engine._save

        def cancel_after_index_commit(cache):
            original(cache)
            if cache:
                cancelled.set()

        with patch.object(self.engine, '_save', cancel_after_index_commit):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(self.engine.prepare({'text': '中断'}, cancelled=cancelled.is_set))
        self.assertEqual(self.cache(), [])
        self.assertEqual(list((self.root / 'tts-cache').glob('*.mp3')), [])
        self.assert_no_pending()

    def test_lru_capacity_and_transient_oversize(self):
        self.engine = speech.SpeechEngine(self.root, cache_bytes=10, cache_entries=2)
        self.prepare('A')
        self.prepare('B')
        self.prepare('A')
        self.prepare('C')
        self.assertEqual({item['key'][0] for item in self.cache()}, {'A', 'C'})
        self.assertLessEqual(sum(item['size'] for item in self.cache()), 10)
        self.engine = speech.SpeechEngine(self.root, cache_entries=1)
        self.prepare('C')
        self.assertEqual([item['key'][0] for item in self.cache()], ['C'])
        self.engine = speech.SpeechEngine(self.root, cache_bytes=1)
        result = self.prepare('oversize')
        self.assertTrue(result.transient)
        self.assertEqual(result.path.read_bytes(), b'MP3:oversize')
        self.assertEqual(self.cache(), [])
        result.path.unlink()  # Device shells own the returned transient file.

    def test_cache_failures_are_friendly_and_never_fail_constructor(self):
        self.prepare('保持')
        cached = self.root / 'tts-cache' / self.cache()[0]['file']
        original_audio = cached.read_bytes()
        originals = {name: getattr(Path, name) for name in ['mkdir', 'read_text', 'replace', 'unlink']}
        cache_dir = self.root / 'tts-cache'

        def failure(name):
            def wrapped(path, *args, **kwargs):
                reject = (name == 'mkdir' and path == cache_dir
                          or name == 'read_text' and path == cache_dir / 'index.json'
                          or name == 'replace' and path.name.endswith('.pending.json')
                          or name == 'unlink' and path == cached)
                if reject:
                    raise PermissionError('fake cache permission failure')
                return originals[name](path, *args, **kwargs)
            return wrapped

        for name in originals:
            with self.subTest(operation=name), patch.object(Path, name, failure(name)):
                self.engine = speech.SpeechEngine(self.root, cache_bytes=0 if name == 'unlink' else 1024)
                with self.assertRaisesRegex(speech.SpeechError, '缓存'):
                    self.prepare('保持')
            self.assertEqual(cached.read_bytes(), original_audio)
        self.assertEqual(len(self.calls), 1)

    def test_unknown_files_are_preserved_when_index_is_missing_or_corrupt(self):
        cache_dir = self.root / 'tts-cache'
        cache_dir.mkdir()
        unknown = cache_dir / ('b' * 32 + '.mp3')
        unknown.write_bytes(b'user file')
        for content in ['invalid JSON', '{"unrecognized":true}']:
            (cache_dir / 'index.json').write_text(content)
            with self.assertRaises(speech.SpeechError):
                self.prepare()
            self.assertEqual(unknown.read_bytes(), b'user file')
            self.assertEqual((cache_dir / 'index.json').read_text(), content)
        (cache_dir / 'index.json').unlink()
        self.prepare()
        self.assertEqual(unknown.read_bytes(), b'user file')

    def test_symlink_cache_directory_is_rejected_without_writing_target(self):
        target = self.root / 'other'; target.mkdir()
        marker = target / 'keep.txt'; marker.write_text('user file')
        try:
            (self.root / 'tts-cache').symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest('This host cannot create symlinks')
        with self.assertRaisesRegex(speech.SpeechError, '符号链接'):
            self.prepare()
        self.assertEqual(list(target.iterdir()), [marker])
        self.assertEqual(self.calls, [])

    def test_directory_in_index_is_not_accepted_as_audio_or_deleted(self):
        cache_dir = self.root / 'tts-cache'; cache_dir.mkdir()
        invalid = cache_dir / ('a' * 32 + '.mp3'); invalid.mkdir()
        key = list(speech.normalize({'text': '雨'})) + ['edge-tts:test-1']
        (cache_dir / 'index.json').write_text(json.dumps([
            dict(key=key, file=invalid.name, size=invalid.stat().st_size, used=0)]))
        result = self.prepare('雨')
        self.assertTrue(result.path.is_file())
        self.assertNotEqual(result.path, invalid)
        self.assertTrue(invalid.is_dir())
        self.assertEqual(len(self.calls), 1)

    def test_network_does_not_hold_the_process_shared_cache_lock(self):
        async def scenario():
            both_started = asyncio.Event()
            started = 0
            async def waiting(text, voice, rate, output):
                nonlocal started
                started += 1
                if started == 2:
                    both_started.set()
                await both_started.wait()
                output.write_bytes(('MP3:' + text).encode())
            self.behavior = waiting
            other_engine = speech.SpeechEngine(self.root)
            return await asyncio.wait_for(asyncio.gather(
                self.engine.prepare({'text': 'A'}), other_engine.prepare({'text': 'B'})), timeout=3)
        results = asyncio.run(scenario())
        self.assertTrue(all(result.path.is_file() for result in results))
        self.assertEqual({item['key'][0] for item in self.cache()}, {'A', 'B'})

    def test_publication_failures_leave_no_valid_audio(self):
        original = Path.replace
        for suffix in ['.pending.mp3', '.pending.json']:
            with self.subTest(suffix=suffix):
                def fail(path, target):
                    # Permit the empty preflight index, then fail the final nonempty publication.
                    if path.name.endswith(suffix) and (suffix == '.pending.mp3' or path.read_text() != '[]'):
                        raise PermissionError('fake publication failure')
                    return original(path, target)
                with patch.object(Path, 'replace', fail), self.assertRaises(speech.SpeechError):
                    self.prepare('保存')
                self.assertEqual(self.cache(), [])
                self.assertEqual(list((self.root / 'tts-cache').glob('*.mp3')), [])
                self.assert_no_pending()

    def test_multiple_processes_reload_index_and_publish_without_lost_entries(self):
        child = '''import asyncio, json, sys, types
from pathlib import Path
provider=types.ModuleType('edge_tts'); provider.__version__='test-1'
class Communicate:
    def __init__(self, **params): self.params=params
    async def save(self, output):
        await asyncio.sleep(0.1)
        Path(output).write_bytes(('MP3:'+self.params['text']).encode())
provider.Communicate=Communicate; sys.modules['edge_tts']=provider
sys.path.insert(0,sys.argv[1])
from speech import SpeechEngine
result=asyncio.run(SpeechEngine(sys.argv[2]).prepare({'text':sys.argv[3]}))
print(json.dumps({'path':str(result.path),'transient':result.transient}))
'''
        processes = [subprocess.Popen([sys.executable, '-B', '-c', child, str(ROOT / 'backend'),
                                       str(self.root), word], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True) for word in ['A', 'B', 'C', 'B']]
        results = []
        try:
            for process in processes:
                output, error = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, error)
                results.append(json.loads(output))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill(); process.wait()
        self.assertEqual({item['key'][0] for item in self.cache()}, {'A', 'B', 'C'})
        self.assertEqual(results[1]['path'], results[3]['path'])
        self.assertEqual(len(list((self.root / 'tts-cache').glob('*.mp3'))), 3)
        self.assert_no_pending()


if __name__ == '__main__':
    unittest.main()
