"""Shared Edge TTS synthesis and bounded local audio cache for desktop shells."""
import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import time
import uuid

import edge_tts
import portalocker


class SpeechError(Exception):
    pass


@dataclass(frozen=True)
class SpeechAudio:
    path: Path
    transient: bool


def normalize(params):
    """Return the same text, Japanese voice and Edge rate on every platform."""
    if not isinstance(params, dict):
        raise SpeechError('朗读参数无效。')
    text = params.get('text')
    if not isinstance(text, str) or not text.strip() or len(text) > 6000:
        raise SpeechError('朗读文本无效，请使用 1 至 6000 个字符。')
    voice = params.get('voice', 'ja-JP-NanamiNeural')
    if not isinstance(voice, str) or not re.fullmatch(r'ja-JP-[A-Za-z]+Neural', voice):
        raise SpeechError('日语音色无效。')
    try:
        rate = float(params.get('rate', 0.42))
    except (TypeError, ValueError):
        raise SpeechError('朗读速度无效。') from None
    if not math.isfinite(rate):
        raise SpeechError('朗读速度无效。')
    rate = max(0.1, min(1.5, rate))
    percent = max(-50, min(100, int((rate - 0.5) * 200)))
    return text, voice, f'{percent:+d}%'


class SpeechEngine:
    def __init__(self, data_dir, *, cache_bytes=32 * 1024 * 1024,
                 cache_entries=128, synthesis_timeout=45):
        # Cache failures are reported by prepare(), never while the app window is opening.
        self.directory = Path(data_dir).absolute() / 'tts-cache'
        self.cache_bytes = max(0, int(cache_bytes))
        self.cache_entries = max(0, int(cache_entries))
        self.synthesis_timeout = synthesis_timeout
        self.engine = 'edge-tts:' + edge_tts.__version__

    @staticmethod
    def _check_cancelled(cancelled):
        if cancelled():
            raise asyncio.CancelledError()

    @contextmanager
    def _locked(self):
        try:
            if self.directory.is_symlink():
                raise SpeechError('语音缓存目录不能是符号链接，请使用本地数据目录。')
            self.directory.mkdir(parents=True, exist_ok=True)
            with portalocker.Lock(self.directory / '.cache.lock', mode='a', timeout=2,
                                  check_interval=0.05, flags=portalocker.LOCK_EX | portalocker.LOCK_NB):
                yield
        except (OSError, portalocker.exceptions.LockException) as error:
            raise SpeechError('语音缓存暂时无法使用，请检查本地文件权限后重试。') from error

    def _load(self):
        """Reload under the process-shared lock; never delete unindexed files."""
        try:
            entries = json.loads((self.directory / 'index.json').read_text(encoding='utf-8'))
        except FileNotFoundError:
            entries = []
        except (ValueError, UnicodeError) as error:
            raise SpeechError('语音缓存索引无法读取，原文件已保留。') from error
        if not isinstance(entries, list):
            raise SpeechError('语音缓存索引无法读取，原文件已保留。')
        cache, discard = {}, set()
        for entry in entries:
            try:
                key, name, size, used = tuple(entry['key']), entry['file'], entry['size'], float(entry['used'])
                if (len(key) != 4 or not all(isinstance(value, str) for value in key)
                        or not re.fullmatch(r'[0-9a-f]{32}\.mp3', name)
                        or type(size) is not int or size <= 0 or not math.isfinite(used)):
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            path = self.directory / name
            if path.is_symlink() or not path.is_file():
                continue
            try:
                actual_size = path.stat().st_size
            except FileNotFoundError:
                continue
            if key[3] != self.engine or actual_size != size:
                discard.add(name)
                continue
            cache[key] = dict(key=list(key), file=name, size=size, used=used)
        keep = {entry['file'] for entry in cache.values()}
        for name in discard - keep:
            (self.directory / name).unlink(missing_ok=True)
        return cache

    def _save(self, cache):
        pending = self.directory / f'{uuid.uuid4().hex}.pending.json'
        try:
            pending.write_text(json.dumps(list(cache.values()), ensure_ascii=False), encoding='utf-8')
            pending.replace(self.directory / 'index.json')
        finally:
            self._remove(pending)

    def _trim(self, cache, *, keep=None):
        total = sum(entry['size'] for entry in cache.values())
        for key, entry in sorted(cache.items(), key=lambda item: item[1]['used']):
            if total <= self.cache_bytes and len(cache) <= self.cache_entries:
                break
            if key == keep and entry['size'] <= self.cache_bytes and self.cache_entries > 0:
                continue
            (self.directory / entry['file']).unlink(missing_ok=True)
            total -= entry['size']
            del cache[key]

    @staticmethod
    def _remove(path):
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass  # A cleanup failure must not hide cancellation or the original error.

    def _cached(self, cache, key):
        entry = cache.get(key)
        if entry is None:
            return None
        entry['used'] = time.time()
        self._trim(cache, keep=key)
        self._save(cache)
        if key in cache:
            return SpeechAudio(self.directory / entry['file'], False)
        return None

    async def prepare(self, params, *, cancelled=lambda: False):
        text, voice, rate = normalize(params)
        key = (text, voice, rate, self.engine)
        pending = self.directory / f'{uuid.uuid4().hex}.pending.mp3'
        created = None
        completed = False
        try:
            self._check_cancelled(cancelled)
            with self._locked():
                self._check_cancelled(cancelled)
                cache = self._load()
                cached = self._cached(cache, key)
                if cached is not None:
                    self._check_cancelled(cancelled)
                    return cached
                self._trim(cache)
                self._save(cache)
            # Synthesis and streaming writes never hold the cross-process cache lock.
            try:
                communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
                await asyncio.wait_for(communicate.save(str(pending)), timeout=self.synthesis_timeout)
            except TimeoutError as error:
                raise SpeechError('Edge TTS 语音生成超时，请检查网络后重试。') from error
            except Exception as error:
                raise SpeechError('Edge TTS 暂时无法生成日语语音，请检查网络后重试。') from error
            self._check_cancelled(cancelled)
            with self._locked():
                self._check_cancelled(cancelled)
                cache = self._load()
                # Another process may have generated the same speech while this one waited.
                cached = self._cached(cache, key)
                if cached is not None:
                    self._check_cancelled(cancelled)
                    return cached
                size = pending.stat().st_size
                if size == 0:
                    raise SpeechError('Edge TTS 未返回有效音频，请稍后重试。')
                created = pending.with_name(pending.name.replace('.pending.mp3', '.mp3'))
                pending.replace(created)
                transient = size > self.cache_bytes or self.cache_entries == 0
                try:
                    if not transient:
                        cache[key] = dict(key=list(key), file=created.name, size=size, used=time.time())
                        self._trim(cache, keep=key)
                        self._check_cancelled(cancelled)
                        self._save(cache)
                    self._check_cancelled(cancelled)
                except BaseException:
                    # Roll back only this request's file, while the same process lock is held.
                    if not transient:
                        cache.pop(key, None)
                        try:
                            self._save(cache)
                        except OSError:
                            pass
                    self._remove(created)
                    raise
                completed = True
                return SpeechAudio(created, transient)
        except OSError as error:
            raise SpeechError('语音缓存暂时无法使用，请检查本地文件权限后重试。') from error
        finally:
            self._remove(pending)
            if not completed:
                self._remove(created)
