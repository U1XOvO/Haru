"""Build platform-native, self-contained GitHub Release artifacts."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import zipfile

from app_config import bundle_info
from release_utils import ROOT, version


def target():
    machine = platform.machine().lower()
    if sys.platform == 'win32' and machine in ('amd64', 'x86_64'):
        return 'windows-x64'
    if sys.platform == 'darwin' and machine in ('arm64', 'x86_64'):
        return 'macos-' + ('arm64' if machine == 'arm64' else 'x64')
    raise RuntimeError('Release builds require macOS arm64/x64 or Windows x64.')


def manifest(folder):
    metadata = {'app': 'Haru', 'version': version(), 'target': target(),
                'python': platform.python_version(), 'signing': 'unsigned' if sys.platform == 'win32' else 'ad-hoc; not notarized',
                'dependencies': sorted(
                    ({'name': item.metadata['Name'], 'version': item.version}
                     for item in importlib.metadata.distributions()), key=lambda item: item['name'].lower())}
    (folder / 'release-info.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    # Preserve installed third-party license texts when their wheels provide them.
    notices = folder / 'third-party-licenses'
    notices.mkdir(exist_ok=True)
    for item in importlib.metadata.distributions():
        for entry in item.files or []:
            if entry.is_absolute() or '..' in entry.parts:
                continue
            if any(part.lower().startswith(('license', 'licence', 'copying', 'notice')) for part in entry.parts):
                source = Path(item.locate_file(entry))
                if source.is_file():
                    destination = notices / item.metadata['Name'] / Path(str(entry))
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)


def build_mac():
    app = ROOT / 'dist/Haru.app'
    # This directory contains build output only; persistent data is in Application Support.
    if app.exists():
        shutil.rmtree(app)
    contents = app / 'Contents'
    resources = contents / 'Resources'
    (contents / 'MacOS').mkdir(parents=True)
    resources.mkdir()
    env = dict(os.environ, MACOSX_DEPLOYMENT_TARGET='14.0',
               PYINSTALLER_CONFIG_DIR=str(ROOT / '.cache/pyinstaller'))
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir',
        '--name', 'HaruBackend', '--paths', str(ROOT / 'backend'),
        '--add-data', str(ROOT / 'data') + ':data',
        '--distpath', str(ROOT / 'build/release-backend'),
        '--workpath', str(ROOT / 'build/release-pyinstaller'),
        '--specpath', str(ROOT / 'build/release-spec'),
        str(ROOT / 'backend/bridge.py')], cwd=ROOT, env=env, check=True)
    shutil.copytree(ROOT / 'build/release-backend/HaruBackend', resources / 'backend-runtime', symlinks=True)
    shutil.copytree(ROOT / 'ui', resources / 'ui')
    subprocess.run(['bash', str(ROOT / 'scripts/build_icons.sh')], cwd=ROOT, check=True)
    shutil.copyfile(ROOT / 'assets/Haru.icns', resources / 'Haru.icns')
    cache = ROOT / 'build/release-swift-cache'
    cache.mkdir(exist_ok=True)
    subprocess.run(['xcrun', 'swiftc', str(ROOT / 'native/Haru.swift'), '-swift-version', '5', '-O',
        '-target', platform.machine() + '-apple-macosx14.0', '-module-cache-path', str(cache),
        '-framework', 'AppKit', '-framework', 'WebKit', '-framework', 'AVFoundation',
        '-o', str(contents / 'MacOS/Haru')], cwd=ROOT, env=env, check=True)
    (contents / 'Info.plist').write_bytes(plistlib.dumps(bundle_info(version())))
    (resources / 'runtime.json').write_text(json.dumps({'mode': 'bundled'}), encoding='utf-8')
    manifest(resources)
    subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(app)], check=True)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)


def build():
    target()
    if sys.platform == 'win32':
        from build_windows import build as build_windows
        build_windows(force=True)
        manifest(ROOT / 'dist/Haru')
    else:
        build_mac()


def package():
    release_info = ROOT / ('dist/Haru.app/Contents/Resources/release-info.json'
                           if sys.platform == 'darwin' else 'dist/Haru/release-info.json')
    info = json.loads(release_info.read_text(encoding='utf-8'))
    if info.get('version') != version() or info.get('target') != target():
        raise RuntimeError('Build version/platform does not match; rebuild before packaging.')
    output = ROOT / 'dist/releases'
    output.mkdir(exist_ok=True)
    archive = output / f'Haru-v{version()}-{target()}.zip'
    temporary = archive.with_suffix('.pending.zip')
    if sys.platform == 'darwin':
        subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent',
                        str(ROOT / 'dist/Haru.app'), str(temporary)], check=True)
    else:
        shutil.make_archive(str(temporary)[:-4], 'zip', ROOT / 'dist', 'Haru')
    with zipfile.ZipFile(temporary) as bundle:
        for entry in bundle.namelist():
            path = Path(entry)
            if path.name == '.env' or path.suffix in ('.sqlite3', '.db') or '.venv' in path.parts:
                raise RuntimeError('Private or development data found in release: ' + entry)
    temporary.replace(archive)
    print(archive)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['build', 'package', 'version', 'verify-tag'])
    parser.add_argument('--tag', default=os.environ.get('GITHUB_REF_NAME'))
    args = parser.parse_args()
    if args.action == 'version':
        print(version())
    elif args.action == 'verify-tag':
        if args.tag != 'v' + version():
            raise SystemExit('Tag must equal v' + version() + ' from pyproject.toml')
    else:
        globals()[args.action]()


if __name__ == '__main__':
    main()
