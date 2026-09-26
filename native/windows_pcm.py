"""Bounded native waveOut PCM queue (24 kHz, mono, signed little-endian 16-bit)."""
from collections import deque
import ctypes as C


class WaveFormat(C.Structure):
    _pack_ = 1
    _fields_ = [('tag', C.c_uint16), ('channels', C.c_uint16), ('rate', C.c_uint32),
                ('bytes_per_second', C.c_uint32), ('alignment', C.c_uint16),
                ('bits', C.c_uint16), ('extra', C.c_uint16)]


class WaveHeader(C.Structure):
    _fields_ = [('data', C.c_void_p), ('length', C.c_uint32), ('recorded', C.c_uint32),
                ('user', C.c_size_t), ('flags', C.c_uint32), ('loops', C.c_uint32),
                ('next', C.c_void_p), ('reserved', C.c_size_t)]


class PCMPlayer:
    """Owner serializes operations with its existing audio lifecycle lock."""
    def __init__(self, api=None):
        self.api = api or C.WinDLL('winmm')
        self.handle = C.c_void_p()
        self.buffers = deque()
        self.paused = self.ended = self.closed = False
        self.size = 0
        signatures = {
            'waveOutOpen': [C.POINTER(C.c_void_p), C.c_uint32, C.POINTER(WaveFormat), C.c_size_t, C.c_size_t, C.c_uint32],
            'waveOutPrepareHeader': [C.c_void_p, C.POINTER(WaveHeader), C.c_uint32],
            'waveOutWrite': [C.c_void_p, C.POINTER(WaveHeader), C.c_uint32],
            'waveOutUnprepareHeader': [C.c_void_p, C.POINTER(WaveHeader), C.c_uint32],
            **{name: [C.c_void_p] for name in ('waveOutPause', 'waveOutRestart', 'waveOutReset', 'waveOutClose')},
        }
        for name, signature in signatures.items():
            function = getattr(self.api, name)
            function.argtypes = signature
            function.restype = C.c_uint32
        fmt = WaveFormat(1, 1, 24000, 48000, 2, 16, 0)
        self._check(self.api.waveOutOpen(C.byref(self.handle), 0xffffffff, C.byref(fmt), 0, 0, 0))

    @staticmethod
    def _check(code):
        if code: raise OSError('无法播放流式音频，请检查输出设备。')

    def _reap(self):
        while self.buffers and self.buffers[0][1].flags & 1:  # WHDR_DONE
            _, header = self.buffers[0]
            self._check(self.api.waveOutUnprepareHeader(self.handle, C.byref(header), C.sizeof(header)))
            self.buffers.popleft()

    def append(self, raw):
        if self.closed or self.ended or not raw or len(raw) % 2 or self.size + len(raw) > 20 * 1024 * 1024:
            raise OSError('朗读音频流无效。')
        self._reap()
        buffer = C.create_string_buffer(raw)
        header = WaveHeader(data=C.cast(buffer, C.c_void_p), length=len(raw))
        self._check(self.api.waveOutPrepareHeader(self.handle, C.byref(header), C.sizeof(header)))
        # Keep both objects alive until the driver has returned the buffer.
        self.buffers.append((buffer, header))
        self._check(self.api.waveOutWrite(self.handle, C.byref(header), C.sizeof(header)))
        self.size += len(raw)

    @property
    def state(self):
        if self.closed: return 'idle'
        self._reap()
        if self.ended and not self.buffers: return 'idle'
        return 'paused' if self.paused else 'playing'

    def toggle_pause(self):
        if self.state == 'idle': return
        function = self.api.waveOutRestart if self.paused else self.api.waveOutPause
        self._check(function(self.handle))
        self.paused = not self.paused

    def finish(self):
        self.ended = True

    def stop(self):
        if self.closed: return
        self._check(self.api.waveOutReset(self.handle))
        self._reap()
        self._check(self.api.waveOutClose(self.handle))
        self.closed = True
