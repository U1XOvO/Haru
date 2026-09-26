"""Bounded subprocess IPC shared by the Windows shell and offline tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app_paths import resource_root

ROOT = resource_root()
BACKEND_ACTIONS = frozenset('''import_legacy prepare_update recover_storage config_get config_save speech_settings_get speech_settings_save speech_voice_create grammar_catalog grammar_detail grammar_mark
grammar_practice study_catalog study_import study_generate study_generation_start study_generation_step
study_generation_status study_generation_cancel study_delete study_start study_attempt study_save
study_history study_mistakes study_retry study_summary study_image annotate dictionary dictionary_add
encounter encounters reading_lookup knowledge daily_word daily_word_add bootstrap profile lesson grade cards
card_queue cards_page card_detail card_create card_random card_seed review decode quiz immersion export ping
history curriculum stage_assessment remedial'''.split())


class DesktopError(Exception):
    pass


class Backend:
    def __init__(self, data_dir, *, bridge=None, deadline=600):
        self.data_dir = Path(data_dir)
        self.bridge = Path(bridge) if bridge else ROOT / 'backend/bridge.py'
        self.deadline = deadline
        self.lock = threading.RLock()
        self.processes = set()
        self.closed = False

    @staticmethod
    def _stop(process):
        if process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass

    def close(self):
        with self.lock:
            self.closed = True
            children = list(self.processes)
            for process in children:
                self._stop(process)
        for process in children:
            process.wait(timeout=5)

    def request(self, message, on_progress=None):
        if not isinstance(message, dict) or type(message.get('id')) is not int:
            raise DesktopError('请求格式无效。')
        action, params = message.get('action'), message.get('params', {})
        if not isinstance(action, str) or action not in BACKEND_ACTIONS or not isinstance(params, dict):
            raise DesktopError('操作无效。')
        payload = json.dumps({'action': action, 'params': params, 'stream': on_progress is not None}, ensure_ascii=False).encode('utf-8')
        if len(payload) > 100_000:
            raise DesktopError('输入过长。')
        env = dict(os.environ, HARU_DATA_DIR=str(self.data_dir), PYTHONIOENCODING='utf-8',
                   PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
        with self.lock:
            if self.closed:
                raise DesktopError('应用正在退出。')
            if getattr(sys, 'frozen', False):
                command = [str(Path(sys.executable).with_name('HaruBackend.exe'))]
            else:
                # pythonw has no standard streams; keep IPC on the console interpreter
                # with CREATE_NO_WINDOW and explicit pipes instead.
                python = Path(sys.executable)
                if python.name.lower() == 'pythonw.exe':
                    python = python.with_name('python.exe')
                command = [str(python), '-u', str(self.bridge)]
            process = subprocess.Popen(command, cwd=ROOT, env=env,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.processes.add(process)
        timed_out = threading.Event()

        def expire():
            if process.poll() is None:
                timed_out.set()
                self._stop(process)

        timer = threading.Timer(self.deadline, expire)
        timer.daemon = True
        timer.start()
        result = None
        try:
            process.stdin.write(payload)
            process.stdin.close()
            while True:
                line = process.stdout.readline(2_000_001)
                if not line:
                    break
                if len(line) > 2_000_000:
                    raise DesktopError('本地服务返回过大。')
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise DesktopError('本地服务返回格式无效。')
                if event.get('event') == 'generation':
                    if on_progress is not None: on_progress(event)
                    continue
                result = event
            process.wait()
            if timed_out.is_set():
                raise DesktopError('操作超过10分钟上限，后台任务已停止。请确认任务状态后重试。')
            if not result or type(result.get('ok')) is not bool:
                raise DesktopError('生成已停止或连接中断，请重试。')
            return result
        finally:
            timer.cancel()
            self._stop(process)
            process.wait()
            process.stdin.close()
            process.stdout.close()
            with self.lock:
                self.processes.discard(process)
