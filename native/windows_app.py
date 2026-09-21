"""Windows WebView2 desktop shell. Only the local UI receives the RPC bridge."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit
import uuid
import webbrowser

from desktop_bridge import Backend, DesktopError, ROOT

ALLOWED_HOSTS = {'www.jpf.go.jp', 'www.jlpt.jp', 'bunpro.jp', 'www.irodori.jpf.go.jp'}


def is_app_url(url, index=None):
    parsed = urlsplit(url)
    expected = urlsplit((index or ROOT / 'ui/index.html').as_uri())
    return (parsed.scheme == 'file' and not parsed.netloc and not parsed.query
            and unquote(parsed.path).casefold() == unquote(expected.path).casefold())


def prepare_ui():
    # pywebview's promise callbacks use eval. Limit this CSP adjustment to the
    # generated Windows copy, preserving the stricter macOS/preview document.
    folder = ROOT / 'build/windows-ui'
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
        self.backend = Backend(self.data_dir, self._stream)

    def _stream(self, request_id, event):
        self.window.run_js(f'window.haruStream({request_id},{json.dumps(event, ensure_ascii=True)})')

    def _recording_stopped(self, error):
        self.window.run_js('window.haruRecordingStopped()')
        if error:
            self.window.run_js(f'toast({json.dumps(error)},true)')

    def _request(self, message):
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
            if action in {'speak', 'record_start', 'record_stop', 'record_play', 'study_stop_audio'}:
                data = self.audio.perform(action, params)
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
                return self.backend.request(message)
            return {'ok': True, 'data': data}
        except DesktopError as error:
            return {'ok': False, 'error': str(error)}
        except Exception:
            return {'ok': False, 'error': '本地操作未完成，请检查文件、系统语音或设备设置后重试。已有记录已保留。'}

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
    host = Host(os.environ.get('HARU_DATA_DIR', ROOT / 'runtime'), prepare_ui())
    try:
        host.audio = WindowsAudio(host.data_dir, host._recording_stopped)
        webview.settings['ALLOW_FILE_URLS'] = False
        webview.settings['ALLOW_DOWNLOADS'] = False
        host.window = webview.create_window('Haru · 日语，在日常里', host.index.as_uri(),
            js_api=API(host), width=1280, height=900, min_size=(840, 660), text_select=True,
            background_color='#f7f9fc')
        host.window.events.before_show += host._before_show
        host.window.events.initialized += host._initialized
        host.window.events.closing += host._close
        webview.start(gui='edgechromium', http_server=False, user_agent='HaruDesktop/Windows',
                      storage_path=str(host.data_dir / 'webview'), private_mode=True)
        return 0 if host.renderer_ready else 1
    except Exception:
        print('Haru could not open WebView2. Install Microsoft Edge WebView2 Runtime (x64), '
              'then run start.cmd again. See README.md.', file=sys.stderr)
        return 1
    finally:
        host._close()


if __name__ == '__main__':
    sys.exit(main())
