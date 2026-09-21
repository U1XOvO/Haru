"""Record local build inputs, without hashing files or reading user configuration."""
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import sys
from release_utils import version

ROOT = Path(__file__).resolve().parents[1]
CONTENTS = ROOT / 'Haru.app/Contents'
INPUTS = (
    'native/Haru.swift', 'scripts/build_app.sh', 'scripts/build_icons.sh',
    'scripts/make_icon.swift', 'scripts/app_config.py', 'scripts/setup_env.sh',
    'scripts/uv.sh', 'pyproject.toml', 'uv.lock', '.python-version',
    'scripts/release_utils.py',
)


def current_config():
    # Values stay strings so the native shell can decode [String: String].
    inputs = {name: [(ROOT / name).stat().st_size, (ROOT / name).stat().st_mtime_ns]
              for name in INPUTS}
    return {'root': str(ROOT), 'python': str(ROOT / '.venv/bin/python'),
            'build_inputs': json.dumps(inputs, sort_keys=True),
            'python_version': sys.version, 'python_base': sys.base_prefix,
            'architecture': platform.machine()}


def is_current():
    try:
        stored = json.loads((CONTENTS / 'Resources/runtime.json').read_text())
        return (stored == current_config()
                and os.access(CONTENTS / 'MacOS/Haru', os.X_OK)
                and (CONTENTS / 'Info.plist').is_file()
                and (CONTENTS / 'Resources/Haru.icns').is_file()
                and (ROOT / 'ui/brand-icon.png').is_file())
    except (OSError, ValueError):
        return False


def bundle_info(release_version):
    return {'CFBundleName': 'Haru', 'CFBundleDisplayName': 'Haru 日语',
            'CFBundleIdentifier': 'local.haru.japanese', 'CFBundleExecutable': 'Haru',
            'CFBundlePackageType': 'APPL', 'CFBundleIconFile': 'Haru.icns',
            'CFBundleShortVersionString': release_version, 'CFBundleVersion': release_version,
            'LSMinimumSystemVersion': '14.0', 'NSHighResolutionCapable': True,
            'NSMicrophoneUsageDescription': '录制你的日语跟读并在本机回放。录音不会上传到云端。',
            'NSHumanReadableCopyright': 'Haru · Japanese Learner for Chinese Speakers'}


def write_config():
    info = bundle_info(version(ROOT))
    for path, data in ((CONTENTS / 'Info.plist', plistlib.dumps(info)),
                       (CONTENTS / 'Resources/runtime.json', json.dumps(current_config()).encode())):
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_bytes(data)
        temporary.replace(path)


def prepare_cache():
    # Swift module caches embed absolute paths and cannot survive a project move.
    cache = ROOT / 'build/swift-cache'
    marker = cache / '.project-root'
    if cache.is_symlink():
        raise RuntimeError('Swift 缓存目录不能是符号链接。')
    if not marker.exists() or marker.read_text() != str(ROOT):
        if cache.exists():
            shutil.rmtree(cache)
        cache.mkdir(parents=True)
        marker.write_text(str(ROOT))


if __name__ == '__main__':
    if sys.argv[1:] == ['check']:
        sys.exit(0 if is_current() else 1)
    if sys.argv[1:] == ['prepare']:
        prepare_cache()
        sys.exit(0)
    if sys.argv[1:] != ['write']:
        sys.exit('Usage: app_config.py check|write|prepare')
    write_config()
