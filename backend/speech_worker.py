"""Cancellable Edge TTS IPC worker; no learning database is opened here."""
import asyncio
import os
from pathlib import Path
import re
import signal
import threading

from app_paths import storage_root
from llm import AppError
from speech import SpeechEngine, SpeechError


def _audio_path(value, data_dir):
    """Expose only a controlled cache filename, never a caller-provided path."""
    path = Path(value)
    cache = data_dir / 'tts-cache'
    folder = cache.resolve()
    if (not re.fullmatch(r'[a-f0-9]{32}\.mp3', path.name)
            or cache.is_symlink() or path.is_symlink() or not path.is_file()
            or path.resolve().parent != folder):
        raise AppError('朗读音频文件无效，请重新播放。')
    return path


def prepare(params, data_dir=None):
    if not isinstance(params, dict):
        raise AppError('朗读请求格式无效。')
    directory = Path(data_dir or os.environ.get('HARU_DATA_DIR') or storage_root() / 'runtime')
    cancelled = threading.Event()
    state = {'loop': None, 'task': None}

    def stop(_number, _frame):
        cancelled.set()
        loop, task = state['loop'], state['task']
        if loop is not None and task is not None and not task.done():
            loop.call_soon_threadsafe(task.cancel)

    async def run():
        if cancelled.is_set():
            return {'cancelled': True}
        state['loop'] = asyncio.get_running_loop()
        try:
            state['task'] = asyncio.create_task(
                SpeechEngine(directory).prepare(params, cancelled=cancelled.is_set))
            result = await state['task']
            path = _audio_path(result.path, directory)
            if cancelled.is_set():
                if result.transient:
                    path.unlink(missing_ok=True)
                return {'cancelled': True}
            return {'audio': path.name, 'transient': bool(result.transient)}
        except asyncio.CancelledError:
            # The shared engine's finally block removes its own pending files.
            return {'cancelled': True}
        except SpeechError as error:
            raise AppError(str(error)) from None

    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, stop)
    try:
        return asyncio.run(run())
    finally:
        signal.signal(signal.SIGTERM, previous)
