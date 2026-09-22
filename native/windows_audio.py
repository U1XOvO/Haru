"""Windows system speech and local WAV recording; no audio leaves this machine."""
"""Test for better Voice"""
import asyncio
import tempfile
import uuid

import edge_tts
"""Test for better Voice"""

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
        self._tts_dir = Path(tempfile.gettempdir()) / 'haru-tts'
        self._tts_dir.mkdir(parents=True, exist_ok=True)
        self._current_tts_file = None

    def _command(self, text, *, optional=False):
        if self.mci(text, None, 0, None) and not optional:
            raise DesktopError('音频操作失败，请检查麦克风权限、音频设备或文件格式。')

    def _stop_audio(self):
        self._command('close haru_play', optional=True)
        if self.speech is not None:
            self.speech.SpeakAsyncCancelAll()

    @staticmethod
    def _rate_to_edge(rate):
        """前端 0.0~1.0 速率 → edge-tts 的百分比字符串。"""
        try:
            r = float(rate)
        except (TypeError, ValueError):
            r = 0.42
        r = max(0.1, min(1.5, r))
        # 以 0.5 为中心（对齐 AVSpeechUtteranceDefaultSpeechRate 约 0.5 的语义）
        percent = int((r - 0.5) * 200)
        percent = max(-50, min(100, percent))
        return f'{percent:+d}%'

    @staticmethod
    async def _edge_generate(text, voice, rate, output):
        communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
        await communicate.save(str(output))

    def _edge_speak(self, text, voice='ja-JP-NanamiNeural', rate='+0%'):
        """同步：生成 MP3 → 通过 mci 播放。失败时抛 DesktopError。"""
        self._stop_audio()

        output = self._tts_dir / f'{uuid.uuid4().hex}.mp3'
        try:
            asyncio.run(self._edge_generate(text, voice, rate, output))
        except RuntimeError:
            # 已有运行中的事件循环（理论上不会发生，防御性处理）
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._edge_generate(text, voice, rate, output))
            finally:
                loop.close()

        if not output.exists() or output.stat().st_size == 0:
            raise DesktopError('语音生成失败，请检查网络后重试。')

        # 只保留最近 5 个缓存，避免 temp 目录无限膨胀
        files = sorted(self._tts_dir.glob('*.mp3'),
                    key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[5:]:
            try:
                old.unlink()
            except OSError:
                pass

        self._current_tts_file = output
        self.play(output)

    def _system_speak(self, text):
        if self.speech is None:
            import clr
            clr.AddReference(
                'System.Speech, Version=4.0.0.0, Culture=neutral, '
                'PublicKeyToken=31bf3856ad364e35'
            )
            from System.Speech.Synthesis import SpeechSynthesizer
            self.speech = SpeechSynthesizer()
        voices = [voice.VoiceInfo for voice in self.speech.GetInstalledVoices()
                if voice.Enabled and str(voice.VoiceInfo.Culture.Name).startswith('ja')]
        if not voices:
            raise DesktopError('未找到桌面日语语音，且 edge-tts 暂时不可用。')
        self._stop_audio()
        self.speech.SelectVoice(voices[0].Name)
        self.speech.Rate = -2
        self.speech.SpeakAsync(text)

    def perform(self, action, params):
        with self.lock:
            if self.closed:
                raise DesktopError('应用正在退出。')
            if action == 'speak':
                text = params.get('text')
                if not isinstance(text, str) or not 0 < len(text) <= 6000:
                    raise DesktopError('朗读文本无效。')
                voice = params.get('voice', 'ja-JP-NanamiNeural')
                if not isinstance(voice, str) or not voice.startswith('ja-JP-'):
                    voice = 'ja-JP-NanamiNeural'
                rate = self._rate_to_edge(params.get('rate', 0.42))
                try:
                    self._edge_speak(text, voice=voice, rate=rate)
                except DesktopError:
                    # edge-tts 需要联网；断网或服务不可用时回退到系统 SAPI
                    self._system_speak(text)
            # if action == 'speak':
            #     text = params.get('text')
            #     if not isinstance(text, str) or not 0 < len(text) <= 6000:
            #         raise DesktopError('朗读文本无效。')
            #     if self.speech is None:
            #         import clr
            #         # Use the full identity so .NET can resolve the system assembly from the GAC.
            #         clr.AddReference(
            #             'System.Speech, Version=4.0.0.0, Culture=neutral, '
            #             'PublicKeyToken=31bf3856ad364e35'
            #         )
            #         from System.Speech.Synthesis import SpeechSynthesizer
            #         self.speech = SpeechSynthesizer()
            #     voices = [voice.VoiceInfo for voice in self.speech.GetInstalledVoices()
            #               if voice.Enabled and str(voice.VoiceInfo.Culture.Name).startswith('ja')]
            #     if not voices:
            #         raise DesktopError('未找到桌面日语语音。请在 Windows 设置中安装日语语音包后重启 Haru。')
            #     self._stop_audio()
            #     self.speech.SelectVoice(voices[0].Name)
            #     self.speech.Rate = -2
            #     self.speech.SpeakAsync(text)
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