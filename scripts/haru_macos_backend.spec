from pathlib import Path
root = Path(SPECPATH).parent
analysis = Analysis([str(root / 'backend/bridge.py')], pathex=[str(root / 'backend')],
    datas=[(str(root / 'build/release/resources/data'), 'data'), (str(root / 'build/release/app-release.json'), '.')],
    excludes=['tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'webview'])
program = EXE(PYZ(analysis.pure), analysis.scripts, [], name='HaruBackend',
    exclude_binaries=True, console=True, upx=False)
COLLECT(program, analysis.binaries, analysis.datas, name='HaruBackend', upx=False)
