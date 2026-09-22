# Build on Windows: the windowed GUI and console IPC worker share one runtime.
from pathlib import Path
import os

root = Path(SPECPATH).parent
paths = [str(root / 'native'), str(root / 'backend')]
icon = str(root / 'build/windows/Haru.ico')
resources = Path(os.environ.get('HARU_RELEASE_RESOURCES', root))
extra_data = []
extra_binaries = []
if os.environ.get('HARU_RELEASE_METADATA'):
    extra_data.append((os.environ['HARU_RELEASE_METADATA'], '.'))
    extra_binaries.append((os.environ['HARU_WINSPARKLE_DLL'], '.'))
else:
    extra_data.append((str(root / 'pyproject.toml'), '.'))
gui = Analysis([str(root / 'native/windows_entry.py')], pathex=paths,
    datas=[(str(resources / 'ui'), 'ui'), (str(resources / 'data'), 'data'), (icon, 'ui')] + extra_data, binaries=extra_binaries,
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
