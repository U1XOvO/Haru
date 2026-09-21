"""Build a reusable standalone Windows app without collecting local user data."""
import json
from pathlib import Path
import subprocess
import sys
from app_version import windows_version

ROOT = Path(__file__).resolve().parents[1]


def inputs(root):
    files = [root / name for name in ('pyproject.toml', 'uv.lock',
        'scripts/build_windows.py', 'scripts/haru_windows.spec', 'scripts/app_version.py')]
    for folder, pattern in [('backend', '*.py'), ('native', '*.py'), ('ui', '*'), ('data', '*.json')]:
        files.extend(path for path in (root / folder).rglob(pattern) if path.is_file())
    return {str(path.relative_to(root)): [path.stat().st_size, path.stat().st_mtime_ns]
            for path in sorted(files)}


def is_current(root=ROOT):
    try:
        manifest = json.loads((root / 'build/windows/build.json').read_text(encoding='utf-8'))
        app = root / 'dist/Haru'
        return (manifest == {'root': str(root.resolve()), 'inputs': inputs(root)}
                and all((app / name).is_file() for name in
                        ('Haru.exe', 'HaruBackend.exe', '_internal/python312.dll',
                         '_internal/ui/Haru.ico', '_internal/ui/index.html',
                         '_internal/data/builtin_lessons.json')))
    except (OSError, ValueError):
        return False


def build(root=ROOT, *, force=False):
    if sys.platform != 'win32':
        raise RuntimeError('Haru.exe must be built on x64 Windows.')
    if not force and is_current(root):
        print('Haru.exe is up to date.')
        return
    from PIL import Image
    folder = root / 'build/windows'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'build.json').unlink(missing_ok=True)
    (folder / 'version.txt').write_text(windows_version(root), encoding='utf-8')
    with Image.open(root / 'ui/brand-icon.png') as icon:
        icon.convert('RGBA').save(folder / 'Haru.ico', format='ICO',
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm',
        '--distpath', str(root / 'dist'), '--workpath', str(folder / 'pyinstaller'),
        str(root / 'scripts/haru_windows.spec')], cwd=root, check=True)
    pending = folder / 'build.pending.json'
    pending.write_text(json.dumps({'root': str(root.resolve()), 'inputs': inputs(root)}), encoding='utf-8')
    pending.replace(folder / 'build.json')
    print('Built: ' + str(root / 'dist/Haru/Haru.exe'))


if __name__ == '__main__':
    if sys.argv[1:] == ['--check']:
        sys.exit(0 if is_current() else 1)
    build()
