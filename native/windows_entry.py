"""Windowed executable entry point; also provides a bounded offline smoke mode."""
import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
import threading


def smoke_test(report):
    import webview
    from windows_app import Host, API
    result = {'ok': False}
    with tempfile.TemporaryDirectory(prefix='haru-app-smoke-') as directory:
        root = Path(directory)
        index = root / 'index.html'
        index.write_text('<html><body>Haru offline smoke</body></html>', encoding='utf-8')
        host = Host(root, index)
        host.window = webview.create_window('Haru offline smoke', index.as_uri(), js_api=API(host))
        host.window.events.before_show += host._before_show
        host.window.events.initialized += host._initialized

        def check():
            try:
                if not host.window.events.loaded.wait(30):
                    raise RuntimeError('WebView2 did not load')
                # Cross the real JS -> Python -> frozen backend boundary.
                finished = threading.Event()
                def receive(value):
                    result['ok'] = value is True and host.renderer_ready
                    finished.set()
                host.window.evaluate_js('''(async () => {
                    const r = await window.pywebview.api.request({id:1, action:'card_seed', params:{}});
                    return r.ok && r.data.length > 0;
                })()''', callback=receive)
                if not finished.wait(30):
                    raise RuntimeError('Frozen IPC did not return')
            except Exception as error:
                result['error_type'] = type(error).__name__
            finally:
                host._close()
                host.window.destroy()

        try:
            webview.start(check, gui='edgechromium', http_server=False, private_mode=True,
                          storage_path=str(root / 'webview'))
        finally:
            host._close()
            report.write_text(json.dumps(result), encoding='utf-8')
    return 0 if result['ok'] else 1


def main():
    # PyInstaller's windowed bootloader leaves standard streams as None.
    for name in ('stdout', 'stderr'):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, 'w', encoding='utf-8'))
    smoke = len(sys.argv) == 3 and sys.argv[1] == '--smoke-test'
    try:
        set_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_id.argtypes = [ctypes.c_wchar_p]
        set_id.restype = ctypes.c_long
        set_id('Haru.JapaneseLearner.Desktop')
        if smoke:
            return smoke_test(Path(sys.argv[2]))
        from windows_app import main as launch
        if launch() == 0:
            return 0
    except Exception as error:
        if smoke:
            Path(sys.argv[2]).write_text(json.dumps({'ok': False, 'error_type': type(error).__name__}), encoding='utf-8')
            return 1
    # A GUI launch must never fail silently or expose credentials in a traceback.
    ctypes.windll.user32.MessageBoxW(None,
        'Haru 无法启动。请确认已安装 Microsoft Edge WebView2 Runtime (x64)，'
        '并保留完整的 Haru 应用文件夹。本地构建可重新运行 start.cmd。', 'Haru', 0x10)
    return 1


if __name__ == '__main__':
    sys.exit(main())
