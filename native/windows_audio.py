"""Windows system speech and local WAV recording; no audio leaves this machine."""
import ctypes
from pathlib import Path
import threading

from desktop_bridge import DesktopError


class WindowsAudio:
    def __init__(self, data_dir, stopped):
        self.path = Path(data_dir) / 'speaking-latest.wav'
        self.stopped = stopped
        self.lock = threading.RLock()
        self.recording = False
        self.timer = None
        self.speech = None
        self.closed = False
        self.mci = ctypes.WinDLL('winmm').mciSendStringW
        self.mci.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
        self.mci.restype = ctypes.c_uint

    def _command(self, text, *, optional=False):
        if self.mci(text, None, 0, None) and not optional:
            raise DesktopError('音频操作失败，请检查麦克风权限、音频设备或文件格式。')

    def _stop_audio(self):
        self._command('close haru_play', optional=True)
        if self.speech is not None:
            self.speech.SpeakAsyncCancelAll()

    def perform(self, action, params):
        with self.lock:
            if self.closed:
                raise DesktopError('应用正在退出。')
            if action == 'speak':
                text = params.get('text')
                if not isinstance(text, str) or not 0 < len(text) <= 6000:
                    raise DesktopError('朗读文本无效。')
                if self.speech is None:
                    import clr
                    clr.AddReference('System.Speech')
                    from System.Speech.Synthesis import SpeechSynthesizer
                    self.speech = SpeechSynthesizer()
                voices = [voice.VoiceInfo for voice in self.speech.GetInstalledVoices()
                          if voice.Enabled and str(voice.VoiceInfo.Culture.Name).startswith('ja')]
                if not voices:
                    raise DesktopError('未找到桌面日语语音。请在 Windows 设置中安装日语语音包后重启 Haru。')
                self._stop_audio()
                self.speech.SelectVoice(voices[0].Name)
                self.speech.Rate = -2
                self.speech.SpeakAsync(text)
            elif action == 'study_stop_audio':
                self._stop_audio()
            elif action == 'record_start':
                if self.recording:
                    raise DesktopError('已经在录音。')
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

    def play(self, path):
        with self.lock:
            if self.closed:
                raise DesktopError('应用正在退出。')
            self._stop_audio()
            kind = 'waveaudio' if Path(path).suffix.lower() == '.wav' else 'mpegvideo'
            self._command(f'open "{path}" type {kind} alias haru_play')
            self._command('play haru_play')

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            try:
                self._finish_recording()
            finally:
                self._stop_audio()
                if self.speech is not None:
                    self.speech.Dispose()
