"""Windows audio devices and request lifecycle; Edge synthesis is shared with macOS."""
import asyncio
import ctypes
from dataclasses import dataclass, field
from pathlib import Path
import threading

from desktop_bridge import DesktopError
from app_paths import storage_root
from speech import SpeechEngine, SpeechError, normalize
from speech_google import prepare_speech
from windows_pcm import PCMPlayer


@dataclass
class SpeechRequest:
    cancelled: threading.Event = field(default_factory=threading.Event)
    loop: object = None
    task: object = None
    progress: object = None


class WindowsAudio:
    def __init__(self, data_dir, stopped, *, cache_bytes=32 * 1024 * 1024,
                 cache_entries=128, synthesis_timeout=45):
        self.path = Path(data_dir) / 'speaking-latest.wav'
        self.data_dir = Path(data_dir)
        self.stopped = stopped
        self.lock = threading.RLock()
        self.recording = False
        self.timer = None
        self.closed = False
        self.mci = ctypes.WinDLL('winmm').mciSendStringW
        self.mci.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
        self.mci.restype = ctypes.c_uint
        self.engine = SpeechEngine(data_dir, cache_bytes=cache_bytes, cache_entries=cache_entries,
                                   synthesis_timeout=synthesis_timeout)
        self._speech_request = None
        self._transient_audio = None
        self.pcm = None

    def _command(self, text, *, optional=False):
        if self.mci(text, None, 0, None) and not optional:
            raise DesktopError('音频操作失败，请检查麦克风权限、音频设备或文件格式。')

    def _stop_audio(self):
        if self.pcm is not None:
            self.pcm.stop()
            self.pcm = None
        self._command('close haru_play', optional=True)
        if self._transient_audio is not None:
            self._remove_transient(self._transient_audio)
            self._transient_audio = None

    def _audio_mode(self):
        if self.pcm is not None: return self.pcm.state
        mode = ctypes.create_unicode_buffer(32)
        if self.mci('status haru_play mode', mode, len(mode), None):
            return 'idle'
        return mode.value.strip().lower()

    @staticmethod
    def _remove_transient(path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _cancel_speech(self):
        """Called with the device lock; cancellation never waits for the network."""
        request, self._speech_request = self._speech_request, None
        if request is not None:
            request.cancelled.set()
            if request.loop is not None and request.task is not None:
                try:
                    request.loop.call_soon_threadsafe(request.task.cancel)
                except RuntimeError:
                    pass  # The synthesis thread has already closed its event loop.

    def _current(self, request):
        return not self.closed and self._speech_request is request and not request.cancelled.is_set()

    async def _prepare_request(self, request, params):
        with self.lock:
            if not self._current(request):
                raise asyncio.CancelledError()
            request.loop = asyncio.get_running_loop()
            request.task = asyncio.current_task()
        def on_audio(phase, raw):
            with self.lock:
                if not self._current(request): raise asyncio.CancelledError()
                if phase == 'reset':
                    self._stop_audio()
                else:
                    if self.pcm is None: self.pcm = PCMPlayer()
                    self.pcm.append(raw)
                if request.progress is not None:
                    request.progress({'event': 'audio', 'phase': phase})
        try:
            return await prepare_speech(params, self.data_dir,
                                        cancelled=request.cancelled.is_set,
                                        config_dir=storage_root(),
                                        edge_engine=self.engine, on_audio=on_audio)
        finally:
            with self.lock:
                request.loop = request.task = None

    def _edge_speak(self, params, on_progress=None):
        request = SpeechRequest(progress=on_progress)
        audio = None
        played = False
        outcome = {}
        with self.lock:
            if self.closed:
                raise DesktopError('应用正在退出。')
            self._cancel_speech()
            self._stop_audio()
            self._speech_request = request
        try:
            # The device lock remains available while the shared engine downloads audio.
            audio = asyncio.run(self._prepare_request(request, params))
            with self.lock:
                if self._current(request):
                    if audio.transient:
                        self._transient_audio = audio.path
                    try:
                        if getattr(audio, 'streamed', False):
                            self.pcm.finish()
                        else:
                            self._play(audio.path)
                    except DesktopError:
                        self._stop_audio()
                        raise
                    played = True
                    outcome = {'engine': getattr(audio, 'engine', 'edge'),
                               'fallback': getattr(audio, 'fallback', '')}
        except asyncio.CancelledError:
            return
        except SpeechError as error:
            with self.lock:
                if self._current(request):
                    self._stop_audio()
                    raise DesktopError(str(error)) from error
        finally:
            if audio is not None and audio.transient and not played:
                self._remove_transient(audio.path)
            with self.lock:
                if self._speech_request is request:
                    self._speech_request = None
        return outcome

    def perform(self, action, params, on_progress=None):
        if action == 'speak':
            try:
                normalize(params)
            except SpeechError as error:
                raise DesktopError(str(error)) from error
            return self._edge_speak(params, on_progress)
        with self.lock:
            if self.closed:
                raise DesktopError('应用正在退出。')
            if action == 'study_stop_audio':
                self._cancel_speech()
                self._stop_audio()
            elif action == 'audio_toggle_pause':
                if self.pcm is not None:
                    self.pcm.toggle_pause()
                    return {'state': self.pcm.state}
                mode = self._audio_mode()
                if mode == 'playing':
                    self._command('pause haru_play')
                    return {'state': 'paused'}
                if mode == 'paused':
                    self._command('resume haru_play')
                    return {'state': 'playing'}
                return {'state': 'idle'}
            elif action == 'audio_status':
                mode = self._audio_mode()
                return {'state': mode if mode in ('playing', 'paused') else 'idle'}
            elif action == 'record_start':
                if self.recording:
                    raise DesktopError('已经在录音。')
                self._cancel_speech()
                self._stop_audio()
                try:
                    self._command('open new type waveaudio alias haru_record')
                    self._command('set haru_record time format milliseconds')
                    self._command('record haru_record to 60000')
                except Exception:
                    self._command('close haru_record', optional=True)
                    raise
                self.recording = True
                self.timer = threading.Timer(60, self._auto_stop)
                self.timer.daemon = True
                self.timer.start()
            elif action == 'record_stop':
                self._finish_recording()
            elif action == 'record_play':
                if self.recording:
                    raise DesktopError('请先停止录音。')
                if not self.path.is_file():
                    raise DesktopError('还没有可回放的录音，请先录下你的跟读。')
                self.play(self.path)
            else:
                raise DesktopError('音频操作无效。')
        return {}

    def _finish_recording(self):
        if not self.recording:
            return
        if self.timer:
            self.timer.cancel()
        temporary = self.path.with_suffix('.pending.wav')
        try:
            self._command('stop haru_record')
            self._command(f'save haru_record "{temporary}"')
        finally:
            self.recording = False
            self._command('close haru_record', optional=True)
        # Windows cannot replace a file while the recording device holds it open.
        temporary.replace(self.path)

    def _auto_stop(self):
        error = None
        with self.lock:
            if self.closed or not self.recording:
                return
            try:
                self._finish_recording()
            except Exception:
                error = '录音保存失败，请检查本地文件权限和音频设备。'
        self.stopped(error)

    def _play(self, path):
        # Callers have already stopped playback, before cache eviction or publication.
        kind = 'waveaudio' if Path(path).suffix.lower() == '.wav' else 'mpegvideo'
        self._command(f'open "{path}" type {kind} alias haru_play')
        self._command('play haru_play')

    def play(self, path):
        with self.lock:
            if self.closed:
                raise DesktopError('应用正在退出。')
            self._cancel_speech()
            self._stop_audio()
            self._play(path)

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self._cancel_speech()
            try:
                self._finish_recording()
            finally:
                self._stop_audio()
