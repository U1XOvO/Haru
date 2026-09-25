"""Offline Google TTS contract, cache, fallback, and cancellation tests."""
import asyncio
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import speech_config
from speech import SpeechAudio, SpeechError
import speech_google
from speech_worker import _audio_path


def wav_data():
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b'\0\0' * 120)
    return stream.getvalue()


def audio_response():
    return httpx.Response(200, json={'steps': [
        {'type': 'model_output', 'content': [
            {'type': 'audio', 'data': base64.b64encode(wav_data()).decode()}]}]})


class EdgeFixture:
    def __init__(self, root, *, fail=False):
        self.root = root
        self.fail = fail
        self.calls = []

    async def prepare(self, params, *, cancelled):
        self.calls.append(params)
        if self.fail:
            raise SpeechError('Edge TTS 不可用。')
        path = self.root / 'edge.mp3'
        path.write_bytes(b'fake mp3')
        return SpeechAudio(path, False)


class SpeechGoogleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / 'runtime'
        self.data.mkdir()
        config = speech_config.defaults()
        config['engine'] = 'gemini'
        config['key'] = 'fixture-secret'
        config['retries'] = 0
        speech_config.save_settings(config, self.root)
        current = speech_config.read_settings(self.root)
        current['voices'][0]['voice_id'] = 'voice_fixture123'
        speech_config._atomic_save(self.root, current)

    def run_speech(self, edge=None, **params):
        edge = edge or EdgeFixture(self.data)
        result = asyncio.run(speech_google.prepare_speech(
            dict(text='今日はいい天気です。', rate=0.42, **params), self.data,
            config_dir=self.root, edge_engine=edge))
        return result, edge

    def test_rest_payload_wav_and_cache_style_key(self):
        calls = []

        async def fake_post(url, key, payload, timeout, retries=0, **options):
            calls.append((url, key, payload, timeout, retries, options))
            return audio_response()

        with patch.object(speech_google, '_post', fake_post):
            first, edge = self.run_speech()
            second, _ = self.run_speech(edge)
            self.assertEqual(len(calls), 1)
            self.assertEqual(first.path, second.path)
            self.assertEqual(first.engine, 'gemini')
            self.assertFalse(first.fallback)
            self.assertEqual(edge.calls, [])
            self.assertEqual(_audio_path(first.path, self.data, 'gemini'), first.path)
            url, key, payload, _, _, _ = calls[0]
            self.assertEqual(url, speech_google.API + '/interactions')
            self.assertEqual(key, 'fixture-secret')
            self.assertEqual(payload['model'], 'gemini-3.8-flash-tts')
            content = payload['input'][0]['content'][0]
            self.assertEqual(content['text'], '今日はいい天気です。')
            self.assertEqual(content['annotations'][0]['type'], 'speech_metadata')
            self.assertNotIn('fixture-secret', json.dumps(payload))
            self.assertEqual(payload['generation_config']['speech_config'][0]['voice'], 'voice_fixture123')
            self.assertEqual(first.path.suffix, '.wav')
            config = speech_config.read_settings(self.root)
            config['pace'] = 'slow'
            speech_config.save_settings(config, self.root)
            changed, _ = self.run_speech(edge)
            self.assertNotEqual(changed.path, first.path)
            self.assertEqual(len(calls), 2)
            self.assertIn('slowly', calls[-1][2]['input'][0]['content'][0]['annotations'][0]['style'])

    def test_quota_or_bad_audio_falls_back_to_edge(self):
        async def quota(*args, **kwargs):
            speech_google._message(httpx.Response(429), voice=True)

        with patch.object(speech_google, '_post', quota):
            result, edge = self.run_speech()
        self.assertEqual(result.engine, 'edge')
        self.assertIn('配额', result.fallback)
        self.assertEqual(len(edge.calls), 1)
        self.assertNotIn('fixture-secret', result.fallback)

        async def invalid(*args, **kwargs):
            return httpx.Response(200, json={'steps': []})

        with patch.object(speech_google, '_post', invalid):
            result, _ = self.run_speech()
        self.assertIn('WAV', result.fallback)

    def test_expired_voice_is_recreated_once(self):
        calls = []
        voice_models = []
        voice_timeouts = []

        async def fake_post(url, key, payload, timeout, retries=0, **options):
            calls.append(url)
            if url.endswith('/voices'):
                voice_models.append(payload['voice']['model'])
                voice_timeouts.append(timeout)
                return httpx.Response(200, json={'id': 'voice_recreated123'})
            if calls.count(speech_google.API + '/interactions') == 1:
                speech_google._message(httpx.Response(404), voice=True)
            return audio_response()

        with patch.object(speech_google, '_post', fake_post):
            result, edge = self.run_speech()
        self.assertEqual(result.engine, 'gemini')
        self.assertEqual(edge.calls, [])
        self.assertEqual(calls.count(speech_google.API + '/voices'), 1)
        self.assertEqual(voice_models, ['gemini-3.8-flash-tts'])
        self.assertEqual(voice_timeouts, [speech_google.VOICE_CREATION_TIMEOUT])
        self.assertEqual(speech_config.read_settings(self.root)['voices'][0]['voice_id'],
                         'voice_recreated123')

    def test_cancel_does_not_start_edge(self):
        edge = EdgeFixture(self.data)

        async def cancelled(*args, **kwargs):
            raise asyncio.CancelledError()

        with patch.object(speech_google, '_post', cancelled):
            with self.assertRaises(asyncio.CancelledError):
                self.run_speech(edge)
        self.assertEqual(edge.calls, [])

    def test_both_online_engines_failure_is_explicit(self):
        edge = EdgeFixture(self.data, fail=True)

        async def quota(*args, **kwargs):
            speech_google._message(httpx.Response(402), voice=True)

        with patch.object(speech_google, '_post', quota):
            with self.assertRaisesRegex(SpeechError, 'Gemini 朗读失败.*Edge TTS 也无法朗读'):
                self.run_speech(edge)

    def test_corrupt_cached_wav_is_regenerated(self):
        calls = []

        async def fake_post(*args, **kwargs):
            calls.append(1)
            return audio_response()

        with patch.object(speech_google, '_post', fake_post):
            first, _ = self.run_speech()
            first.path.write_bytes(b'broken wave' + b'\0' * (len(wav_data()) - 11))
            second, _ = self.run_speech()
        self.assertEqual(len(calls), 2)
        self.assertEqual(second.path.read_bytes(), wav_data())


if __name__ == '__main__':
    unittest.main()
