# Build on Windows: the windowed GUI and console IPC worker share one runtime.
from pathlib import Path

root = Path(SPECPATH).parent
paths = [str(root / 'native'), str(root / 'backend')]
icon = str(root / 'build/windows/Haru.ico')
gui = Analysis([str(root / 'native/windows_entry.py')], pathex=paths,
    datas=[(str(root / 'ui'), 'ui'), (str(root / 'data'), 'data'), (icon, 'ui')],
    hiddenimports=['webview.platforms.winforms', 'webview.platforms.edgechromium'],
    excludes=['tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'webview.platforms.cef'])
backend = Analysis([str(root / 'backend/bridge.py')], pathex=paths)
app = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True,
    name='Haru', console=False, icon=icon, upx=False,
    version=str(root / 'build/windows/version.txt'))
worker = EXE(PYZ(backend.pure), backend.scripts, [('u', None, 'OPTION')],
    exclude_binaries=True, name='HaruBackend', console=True, icon=icon, upx=False)
COLLECT(app, worker, gui.binaries, gui.datas, backend.binaries, backend.datas,
    name='Haru', upx=False)
