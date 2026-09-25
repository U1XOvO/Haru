"""Gemini TTS, designed voices, and fallback to the existing Edge engine."""
import asyncio
import base64
import binascii
from dataclasses import replace
import io
from pathlib import Path
import re
import wave

import httpx
import portalocker

from app_paths import storage_root
from llm import AppError
from speech import SpeechEngine, SpeechError
from speech_config import MODEL, read_settings, style_for, _atomic_save


API = 'https://generativelanguage.googleapis.com/v1beta'
VOICE_ID = re.compile(r'voice_[A-Za-z0-9_-]{1,180}\Z')
MAX_WAV = 20 * 1024 * 1024
VOICE_CREATION_TIMEOUT = 300


class VoiceUnavailable(SpeechError):
    pass


def _message(response, *, voice=False):
    code = response.status_code
    if code == 400 and voice:
        try:
            detail = response.json().get('error', {}).get('message', '')
        except (ValueError, TypeError, AttributeError):
            detail = ''
        if isinstance(detail, str) and 'blocked by safety policies' in detail.lower():
            raise SpeechError('Google 拒绝了音色描述，请改用明确的成年声线描述。')
    if code == 404 and voice:
        raise VoiceUnavailable('Google 音色已失效或不属于当前项目。')
    if code in (401, 403):
        raise SpeechError('Google API Key 无效，或当前项目没有语音模型权限。')
    if code == 402:
        raise SpeechError('Google 项目付费额度不足。')
    if code == 429:
        raise SpeechError('Google 语音配额或速率已达到上限。')
    if code in (408, 500, 502, 503, 504):
        raise SpeechError('Google 语音服务暂时不可用。')
    raise SpeechError('Google 语音请求失败，请检查模型权限或配置。')


async def _post(url, key, payload, timeout, retries=0, *, voice=False):
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            for attempt in range(retries + 1):
                try:
                    response = await client.post(url, headers={'x-goog-api-key': key}, json=payload)
                except (httpx.TimeoutException, httpx.NetworkError, httpx.ProtocolError):
                    if attempt < retries:
                        await asyncio.sleep(0.5 * (attempt + 1))
                        continue
                    raise
                if response.status_code in (408, 429, 500, 502, 503, 504) and attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                if response.is_error:
                    _message(response, voice=voice)
                return response
    except (httpx.TimeoutException, httpx.NetworkError, httpx.ProtocolError) as error:
        raise SpeechError('无法连接 Google 语音服务。') from error


def _json(response):
    try:
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, TypeError) as error:
        raise SpeechError('Google 语音响应格式无效。') from error


async def create_voice(identity, root=None, *, force=False):
    """Create one project-scoped voice per persona; changes are published atomically."""
    root = Path(root) if root is not None else storage_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(root / '.speech-voice.lock', mode='a', timeout=90):
            data = read_settings(root)
            voice = next((v for v in data['voices'] if v['id'] == identity), None)
            if voice is None:
                raise AppError('请选择有效的朗读音色。')
            if not data['key']:
                raise AppError('请先填写 Google AI Studio API Key。')
            if voice['voice_id'] and not force:
                return {'voice': voice['id'], 'ready': True}
            payload = {'store': True, 'voice': {
                'model': MODEL, 'type': 'prompted', 'display_name': voice['name'],
                'gender': voice['gender'], 'language_code': 'ja-JP',
                'prompted': {'input': voice['description']}}}
            response = await _post(API + '/voices', data['key'], payload, VOICE_CREATION_TIMEOUT)
            created = _json(response).get('id', '')
            if not isinstance(created, str) or not VOICE_ID.fullmatch(created):
                raise SpeechError('Google 未返回有效的音色编号。')
            with portalocker.Lock(root / '.speech-settings.lock', mode='a', timeout=10):
                current = read_settings(root)
                target = next((v for v in current['voices'] if v['id'] == identity), None)
                if (target is None or current['key'] != data['key'] or
                        target['description'] != voice['description'] or
                        target['gender'] != voice['gender']):
                    raise AppError('朗读配置已变化，请重新生成音色。')
                target['voice_id'] = created
                current['revision'] += 1
                _atomic_save(root, current)
            return {'voice': identity, 'ready': True}
    except (OSError, portalocker.exceptions.LockException) as error:
        raise AppError('无法保存 Google 音色，请检查本地文件权限。') from error


class GeminiSpeechEngine(SpeechEngine):
    def __init__(self, data_dir, settings, voice_id):
        super().__init__(data_dir, cache_bytes=64 * 1024 * 1024,
                         synthesis_timeout=settings['timeout'] * (settings['retries'] + 1) + 3)
        self.directory = Path(data_dir).absolute() / 'tts-gemini-cache'
        self.extension = '.wav'
        self.engine = MODEL + ':v1'
        self.provider_label = 'Gemini TTS'
        self.settings = settings
        self.voice_id = voice_id

    @staticmethod
    def _valid_cached_audio(path):
        try:
            with wave.open(str(path), 'rb') as audio:
                return (audio.getnchannels() == 1 and audio.getsampwidth() == 2
                        and audio.getframerate() == 24000 and audio.getnframes() > 0
                        and bool(audio.readframes(1)))
        except (OSError, EOFError, wave.Error):
            return False

    def _normalize(self, params):
        if not isinstance(params, dict):
            raise SpeechError('朗读参数无效。')
        text = params.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > 6000:
            raise SpeechError('朗读文本无效，请使用 1 至 6000 个字符。')
        return text, self.voice_id, style_for(self.settings)

    async def _synthesize(self, text, voice, style, pending):
        payload = {'model': MODEL, 'input': [{'type': 'user_input', 'content': [{
            'type': 'text', 'text': text, 'annotations': [{
                'type': 'speech_metadata', 'style': style}]}]}],
            'response_format': {'type': 'audio'},
            'generation_config': {'speech_config': [{'voice': voice}]}}
        response = await _post(API + '/interactions', self.settings['key'], payload,
                               self.settings['timeout'], self.settings['retries'], voice=True)
        steps = _json(response).get('steps', [])
        try:
            sounds = [part['data'] for step in steps if step.get('type') == 'model_output'
                      for part in step.get('content', []) if part.get('type') == 'audio']
            if not sounds:
                raise ValueError()
            raw = base64.b64decode(sounds[-1], validate=True)
            if not 44 <= len(raw) <= MAX_WAV:
                raise ValueError()
            with wave.open(io.BytesIO(raw), 'rb') as audio:
                if (audio.getnchannels() != 1 or audio.getsampwidth() != 2 or
                        audio.getframerate() != 24000 or audio.getnframes() == 0 or
                        not audio.readframes(1)):
                    raise ValueError()
        except (KeyError, TypeError, ValueError, binascii.Error, wave.Error) as error:
            raise SpeechError('Gemini 未返回可播放的 WAV 音频。') from error
        pending.write_bytes(raw)

    async def prepare(self, params, *, cancelled=lambda: False):
        result = await super().prepare(params, cancelled=cancelled)
        return replace(result, engine='gemini')


async def prepare_speech(params, data_dir, *, cancelled=lambda: False,
                         config_dir=None, edge_engine=None):
    root = Path(config_dir) if config_dir is not None else storage_root()
    edge = edge_engine or SpeechEngine(data_dir)
    reason = ''
    try:
        settings = read_settings(root)
    except AppError:
        settings = None
        reason = '朗读配置不可用'
    if settings is not None and settings['engine'] == 'gemini':
        try:
            selected = settings['selected']
            voice = next(v for v in settings['voices'] if v['id'] == selected)
            if not settings['key']:
                raise SpeechError('尚未配置 Google API Key')
            if not voice['voice_id']:
                await create_voice(selected, root)
                settings = read_settings(root)
                voice = next(v for v in settings['voices'] if v['id'] == selected)
            try:
                return await GeminiSpeechEngine(data_dir, settings, voice['voice_id']).prepare(
                    params, cancelled=cancelled)
            except VoiceUnavailable:
                await create_voice(selected, root, force=True)
                settings = read_settings(root)
                voice = next(v for v in settings['voices'] if v['id'] == selected)
                return await GeminiSpeechEngine(data_dir, settings, voice['voice_id']).prepare(
                    params, cancelled=cancelled)
        except asyncio.CancelledError:
            raise
        except (AppError, SpeechError, OSError, httpx.HTTPError) as error:
            reason = str(error) if isinstance(error, (AppError, SpeechError)) else 'Gemini 服务不可用'
    if cancelled():
        raise asyncio.CancelledError()
    try:
        result = await edge.prepare(params, cancelled=cancelled)
        return replace(result, fallback=reason) if reason else result
    except SpeechError as error:
        if reason:
            raise SpeechError(f'Gemini 朗读失败（{reason}）；Edge TTS 也无法朗读：{error}') from error
        raise
