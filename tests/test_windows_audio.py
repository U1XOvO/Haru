"""Windows device lifecycle tests; no Windows hardware, network, or system TTS is used."""
import asyncio
import importlib.util
from pathlib import Path
import re
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'native'), str(ROOT / 'backend')]
provider = types.ModuleType('edge_tts'); provider.__version__ = 'test-1'
spec = importlib.util.spec_from_file_location('haru_windows_shared_speech_tested', ROOT / 'backend/speech.py')
speech = importlib.util.module_from_spec(spec); sys.modules[spec.name] = speech
with patch.dict(sys.modules, {'edge_tts': provider}):
    spec.loader.exec_module(speech)
spec = importlib.util.spec_from_file_location('haru_windows_audio_tested', ROOT / 'native/windows_audio.py')
audio_module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = audio_module
with patch.dict(sys.modules, {'speech': speech}):
    spec.loader.exec_module(audio_module)


class FakeMCI:
    def __init__(self):
        self.commands = []
        self.played = []
        self.opened = None

    def __call__(self, command, *_):
        self.commands.append(command)
        opened = re.fullmatch(r'open "(.+)" type (?:mpegvideo|waveaudio) alias haru_play', command)
        if opened:
            self.opened = Path(opened[1])
            if not self.opened.is_file():
                return 1
        if command == 'play haru_play':
            self.played.append((self.opened, self.opened.read_bytes()))
        saved = re.fullmatch(r'save haru_record "(.+)"', command)
        if saved:
            Path(saved[1]).write_bytes(b'local microphone recording')
        return 0


class WindowsAudioTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='haru-windows-audio-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.mci = FakeMCI()
        self.calls = []
        self.behavior = self.prepare_audio
        patcher = patch.object(audio_module.ctypes, 'WinDLL', create=True,
                               return_value=types.SimpleNamespace(mciSendStringW=self.mci))
        patcher.start(); self.addCleanup(patcher.stop)
        owner = self

        class FakeEngine:
            async def prepare(self, params, *, cancelled):
                owner.calls.append(params)
                return await owner.behavior(params, cancelled)

        patcher = patch.object(audio_module, 'SpeechEngine', return_value=FakeEngine())
        patcher.start(); self.addCleanup(patcher.stop)
        self.audio = audio_module.WindowsAudio(self.root, Mock())
        self.addCleanup(self.audio.close)

    async def prepare_audio(self, params, cancelled):
        path = self.root / (uuid.uuid4().hex + '.mp3')
        path.write_bytes(('MP3:' + params['text']).encode())
        return speech.SpeechAudio(path, transient=False)

    def speak(self, text='こんにちは', **params):
        return self.audio.perform('speak', dict(text=text, **params))

    def start_speech(self, text):
        errors = []
        def run():
            try:
                self.speak(text)
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=run, daemon=True); thread.start()
        return thread, errors

    def finish_thread(self, thread, errors):
        thread.join(2)
        self.assertFalse(thread.is_alive(), 'speech did not stop after cancellation')
        self.assertEqual(errors, [])

    def test_speech_delegates_to_shared_engine_and_plays_its_audio(self):
        self.speak('雨', voice='ja-JP-KeitaNeural', rate=0.7)
        self.assertEqual(self.calls, [{'text': '雨', 'voice': 'ja-JP-KeitaNeural', 'rate': 0.7}])
        self.assertEqual(self.mci.played[-1][1], 'MP3:雨'.encode())
        self.assertFalse(hasattr(self.audio, '_system_speak'))
        with self.assertRaises(audio_module.DesktopError):
            self.speak('a' * 6001)
        self.assertEqual(len(self.calls), 1)

    def test_shared_engine_error_is_reported_without_system_fallback(self):
        async def failed(params, cancelled):
            raise speech.SpeechError('Edge TTS 暂时无法生成日语语音，请检查网络后重试。')
        self.behavior = failed
        with self.assertRaisesRegex(audio_module.DesktopError, 'Edge TTS'):
            self.speak()
        self.assertEqual(self.mci.played, [])
        self.assertIsNone(self.audio._speech_request)

    def test_stop_and_close_cancel_without_holding_device_lock(self):
        for action in ['stop', 'close']:
            with self.subTest(action=action):
                started = threading.Event()
                cancelled_seen = threading.Event()
                async def stalled(params, cancelled):
                    started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        if cancelled():
                            cancelled_seen.set()
                self.behavior = stalled
                thread, errors = self.start_speech('途中')
                self.assertTrue(started.wait(2))
                if action == 'stop':
                    self.audio.perform('study_stop_audio', {})
                else:
                    self.audio.close()
                self.finish_thread(thread, errors)
                self.assertTrue(cancelled_seen.is_set())
                self.assertEqual(self.mci.played, [])
        with self.assertRaises(audio_module.DesktopError):
            self.speak()

    def test_obsolete_result_cannot_play_and_transient_file_is_removed(self):
        started, release = threading.Event(), threading.Event()
        obsolete = self.root / 'obsolete.mp3'
        async def delayed(params, cancelled):
            if params['text'] == '古い':
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    while not release.is_set():
                        await asyncio.sleep(0.001)
                obsolete.write_bytes(b'old')
                return speech.SpeechAudio(obsolete, True)
            return await self.prepare_audio(params, cancelled)
        self.behavior = delayed
        thread, errors = self.start_speech('古い')
        self.assertTrue(started.wait(2))
        try:
            self.speak('新しい')
        finally:
            release.set()
        self.finish_thread(thread, errors)
        self.assertEqual([data for _, data in self.mci.played], ['MP3:新しい'.encode()])
        self.assertFalse(obsolete.exists())

    def test_transient_audio_is_removed_on_stop_and_close(self):
        async def transient(params, cancelled):
            audio = await self.prepare_audio(params, cancelled)
            return speech.SpeechAudio(audio.path, True)
        self.behavior = transient
        self.speak('一時')
        path = self.mci.played[-1][0]
        self.assertTrue(path.exists())
        self.audio.perform('study_stop_audio', {})
        self.assertFalse(path.exists())
        self.speak('終了')
        path = self.mci.played[-1][0]
        self.audio.close()
        self.assertFalse(path.exists())

    def test_imported_audio_replaces_inflight_speech(self):
        started = threading.Event()
        async def stalled(params, cancelled):
            started.set()
            await asyncio.Event().wait()
        self.behavior = stalled
        thread, errors = self.start_speech('中断')
        self.assertTrue(started.wait(2))
        imported = self.root / 'imported.wav'; imported.write_bytes(b'imported audio')
        self.audio.play(imported)
        self.finish_thread(thread, errors)
        self.assertEqual(self.mci.played, [(imported, b'imported audio')])

    def test_recording_remains_local_and_can_be_played(self):
        self.audio.perform('record_start', {})
        self.audio.perform('record_stop', {})
        self.audio.perform('record_play', {})
        self.assertEqual(self.calls, [])
        self.assertEqual(self.audio.path, self.root / 'speaking-latest.wav')
        self.assertEqual(self.mci.played[-1][1], b'local microphone recording')
        self.assertFalse((self.root / 'speaking-latest.pending.wav').exists())


if __name__ == '__main__':
    unittest.main()
