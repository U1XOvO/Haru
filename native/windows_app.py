"""Windows WebView2 desktop shell. Only the local UI receives the RPC bridge."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import portalocker
from urllib.parse import unquote, urlsplit
import uuid
import webbrowser

from desktop_bridge import Backend, DesktopError, ROOT
from app_paths import storage_root, app_info

ALLOWED_HOSTS = {'www.jpf.go.jp', 'www.jlpt.jp', 'bunpro.jp', 'www.irodori.jpf.go.jp'}


def is_app_url(url, index=None):
    parsed = urlsplit(url)
    expected = urlsplit((index or ROOT / 'ui/index.html').as_uri())
    return (parsed.scheme == 'file' and not parsed.netloc and not parsed.query
            and unquote(parsed.path).casefold() == unquote(expected.path).casefold())


def prepare_ui():
    # pywebview's promise callbacks use eval. Limit this CSP adjustment to the
    # generated Windows copy, preserving the stricter macOS/preview document.
    folder = (storage_root() if getattr(sys, 'frozen', False) else ROOT) / 'build/windows-ui'
    shutil.copytree(ROOT / 'ui', folder, dirs_exist_ok=True)
    index = folder / 'index.html'
    html = index.read_text(encoding='utf-8')
    html = html.replace("script-src 'self';", "script-src 'self' 'unsafe-eval';", 1)
    html = html.replace("object-src 'none';", "object-src 'none'; frame-src 'none';", 1)
    index.write_text(html, encoding='utf-8')
    return index


def asset_path(data_dir, identity):
    if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{32}\.(pdf|mp3|m4a|wav|png|jpg)', identity):
        raise DesktopError('媒体标识无效。')
    folder = (data_dir / 'study-assets').resolve()
    path = (folder / identity).resolve()
    if path.parent != folder or not path.is_file():
        raise DesktopError('媒体文件不存在。')
    return path


def import_file(data_dir, path):
    path = Path(path)
    extension = path.suffix.lower().lstrip('.')
    if extension not in {'json', 'pdf', 'mp3', 'm4a', 'wav', 'png', 'jpg', 'jpeg'}:
        raise DesktopError('不支持的文件类型。')
    limit = 95_000 if extension == 'json' else 2_000_000 if extension in {'png', 'jpg', 'jpeg'} else 50_000_000
    with path.open('rb') as source:
        content = source.read(limit + 1)
    if not 0 < len(content) <= limit:
        raise DesktopError('JSON最大95KB，题图最大2MB，PDF或音频最大50MB。')
    if extension == 'json':
        return {'paper': json.loads(content.decode('utf-8-sig')), 'name': path.name}
    folder = data_dir / 'study-assets'
    folder.mkdir(parents=True, exist_ok=True)
    identity = uuid.uuid4().hex + '.' + ('jpg' if extension == 'jpeg' else extension)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
        temporary.replace(folder / identity)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return {'asset': identity, 'name': path.name}


class Host:
    def __init__(self, data_dir, index=None):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.window = None
        self.index = index or ROOT / 'ui/index.html'
        self.audio = None
        self.renderer_ready = False
        self.updater = None
        self.lifecycle_lock = threading.RLock()
        self.active_requests = 0
        self.maintenance = False
        self.update_ready = False
        self.backend = Backend(self.data_dir)

    def _recording_stopped(self, error):
        self.window.run_js('window.haruRecordingStopped()')
        if error:
            self.window.run_js(f'toast({json.dumps(error)},true)')

    def _request(self, message):
        with self.lifecycle_lock:
            if self.maintenance:
                return {'ok': False, 'error': '正在准备更新或迁移，请重新打开 Haru。'}
            self.active_requests += 1
        try:
            return self._dispatch(message)
        finally:
            with self.lifecycle_lock:
                self.active_requests -= 1

    def _dispatch(self, message):
        try:
            if self.backend.closed or not is_app_url(self.window.get_current_url() or '', self.index):
                raise DesktopError('当前页面不能访问本地服务。')
            if (not isinstance(message, dict) or type(message.get('id')) is not int
                    or not isinstance(message.get('action'), str)
                    or not isinstance(message.get('params', {}), dict)):
                raise DesktopError('请求格式无效。')
            if len(json.dumps(message, ensure_ascii=False).encode('utf-8')) > 101_000:
                raise DesktopError('输入过长。')
            action, params = message['action'], message.get('params', {})
            if action == 'app_info':
                data = dict(app_info(), automatic_updates=self.updater.automatic() if self.updater else False)
            elif action == 'check_updates':
                if not self.updater:
                    raise DesktopError('更新服务尚未就绪。')
                self.updater.check()
                data = {}
            elif action == 'update_preferences':
                if not self.updater or type(params.get('enabled')) is not bool:
                    raise DesktopError('更新设置无效。')
                data = {'automatic_updates': self.updater.automatic(params['enabled'])}
            elif action == 'open_downloads':
                webbrowser.open('https://github.com/U1XOvO/Haru/releases')
                data = {}
            elif action == 'import_legacy':
                import webview
                with self.lifecycle_lock:
                    if self.active_requests != 1 or self.audio.recording:
                        raise DesktopError('请等待学习任务结束并停止录音后再导入。')
                selection = self.window.create_file_dialog(webview.FileDialog.FOLDER)
                if not selection:
                    return {'ok': True, 'data': {'cancelled': True}}
                with self.lifecycle_lock:
                    if self.active_requests != 1:
                        raise DesktopError('仍有任务正在保存，请稍后导入。')
                    self.maintenance = True
                result = self.backend.request({'id': message['id'], 'action': 'import_legacy',
                                               'params': {'source': selection[0]}})
                if not result.get('ok'):
                    self.maintenance = False
                return result
            elif action in {'speak', 'audio_toggle_pause', 'audio_status', 'record_start', 'record_stop', 'record_play', 'study_stop_audio'}:
                data = self.audio.perform(action, params, on_progress=lambda event:
                    self.window.run_js(f'window.haruProgress?.({message["id"]},{json.dumps(event)})'))
            elif action == 'study_pick':
                import webview
                selection = self.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False,
                    file_types=('学习资料 (*.json;*.pdf;*.mp3;*.m4a;*.wav;*.png;*.jpg;*.jpeg)',))
                data = import_file(self.data_dir, selection[0]) if selection else {'cancelled': True}
            elif action == 'study_open_asset':
                path = asset_path(self.data_dir, params.get('id'))
                if path.suffix in {'.mp3', '.m4a', '.wav'}:
                    self.audio.play(path)
                else:
                    os.startfile(str(path))
                data = {}
            elif action == 'open_url':
                url = params.get('url', '')
                parsed = urlsplit(url)
                if parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
                    raise DesktopError('仅允许打开已核实的教学来源 HTTPS 地址。')
                webbrowser.open(url)
                data = {}
            elif action == 'reveal':
                raw = params.get('path')
                if not isinstance(raw, str):
                    raise DesktopError('文件路径无效。')
                path = Path(raw).resolve()
                exports = (self.data_dir / 'exports').resolve()
                if not path.is_relative_to(exports) or not path.is_file():
                    raise DesktopError('只能显示应用导出的文件。')
                subprocess.Popen(['explorer.exe', '/select,', str(path)])
                data = {}
            else:
                if action in {'prepare_update', 'recover_storage'}:
                    raise DesktopError('此操作只能由应用内部发起。')
                def progress(event):
                    self.window.run_js(f'window.haruProgress?.({message["id"]},{json.dumps(event, ensure_ascii=False)})')
                return self.backend.request(message, on_progress=progress)
            return {'ok': True, 'data': data}
        except DesktopError as error:
            return {'ok': False, 'error': str(error)}
        except Exception:
            self.maintenance = False
            return {'ok': False, 'error': '本地操作未完成，请检查文件或音频设备设置后重试。已有记录已保留。'}

    def _loaded(self):
        from windows_updater import WindowsUpdater
        try:
            self.updater = WindowsUpdater(self._can_update,
                lambda: threading.Thread(target=self.window.destroy, daemon=True).start(), self._update_failed)
        except Exception:
            self.window.run_js('toast("更新组件未能初始化，请从发布页面下载安装包。",true)')

    def _can_update(self):
        try:
            with self.lifecycle_lock:
                if self.update_ready:
                    return 1
                if self.active_requests or self.maintenance or self.audio.recording:
                    return 0
                self.maintenance = True
            if self.window.evaluate_js('window.haruPrepareUpdate && window.haruPrepareUpdate()') is not True:
                self.maintenance = False
                return 0
            result = self.backend.request({'id': 0, 'action': 'prepare_update', 'params': {}})
            if result.get('ok'):
                self.update_ready = True
                return 1
        except Exception:
            pass
        self.maintenance = False
        self.window.run_js('window.haruCancelUpdate && window.haruCancelUpdate()')
        return 0

    def _update_failed(self):
        if self.update_ready:
            self.update_ready = False
            self.maintenance = False
            self.window.run_js('window.haruCancelUpdate && window.haruCancelUpdate()')

    def _before_show(self):
        # WebView2 navigation is blocked before any external document can get IPC.
        def navigation_starting(sender, args):
            if not is_app_url(str(args.Uri), self.index):
                args.Cancel = True
        self.window.native.webview.NavigationStarting += navigation_starting

    def _initialized(self, renderer):
        # pywebview can fall back to legacy MSHTML even with gui='edgechromium'.
        self.renderer_ready = renderer == 'edgechromium'
        if not self.renderer_ready:
            print('Microsoft Edge WebView2 Runtime (x64) is required. Install it and retry start.cmd.',
                  file=sys.stderr)
            return False

    def _close(self):
        if self.updater:
            self.updater.close()
        self.backend.close()
        if self.audio:
            self.audio.close()


class API:
    # Expose exactly one method; Host/window/process internals are never exposed.
    def __init__(self, host):
        self._host = host

    def request(self, message):
        return self._host._request(message)


def main():
    if sys.platform != 'win32':
        print('Please use start.py to select the desktop shell for this system.', file=sys.stderr)
        return 1
    import webview
    from windows_audio import WindowsAudio
    instance = portalocker.Lock(storage_root() / '.app.lock', mode='a', timeout=0)
    storage_root().mkdir(parents=True, exist_ok=True)
    try:
        instance.acquire()
    except portalocker.exceptions.LockException:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, 'Haru 已在运行，请使用已打开的窗口。', 'Haru', 0x40)
        return 0
    host = Host(os.environ.get('HARU_DATA_DIR', storage_root() / 'runtime'), prepare_ui())
    try:
        host.audio = WindowsAudio(host.data_dir, host._recording_stopped)
        webview.settings['ALLOW_FILE_URLS'] = False
        webview.settings['ALLOW_DOWNLOADS'] = False
        host.window = webview.create_window('Haru · 日语，在日常里', host.index.as_uri(),
            js_api=API(host), width=1280, height=900, min_size=(840, 660), text_select=True,
            background_color='#f7f9fc')
        host.window.events.loaded += host._loaded
        host.window.events.before_show += host._before_show
        host.window.events.initialized += host._initialized
        host.window.events.closing += host._close
        webview.start(gui='edgechromium', http_server=False, user_agent='HaruDesktop/Windows',
                      storage_path=str(host.data_dir / 'webview'), private_mode=True,
                      icon=str(ROOT / 'ui/Haru.ico') if (ROOT / 'ui/Haru.ico').is_file() else None)
        return 0 if host.renderer_ready else 1
    except Exception:
        print('Haru could not open WebView2. Install Microsoft Edge WebView2 Runtime (x64), '
              'then run start.cmd again. See README.md.', file=sys.stderr)
        return 1
    finally:
        host._close()
        instance.release()


if __name__ == '__main__':
    sys.exit(main())
