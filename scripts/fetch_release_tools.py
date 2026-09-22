"""Pinned, project-local upstream updater tools; no global installation."""
from pathlib import Path
import shutil
import tarfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = {'sparkle': '2.10.0', 'winsparkle': '0.9.4'}
URLS = {
    'sparkle': 'https://github.com/sparkle-project/Sparkle/releases/download/2.10.0/Sparkle-2.10.0.tar.xz',
    'winsparkle': 'https://github.com/vslavik/winsparkle/releases/download/v0.9.4/WinSparkle-0.9.4.zip',
}


def fetch(name):
    folder = ROOT / 'build/release-tools' / (name + '-' + VERSIONS[name])
    marker = folder / '.complete'
    if marker.is_file():
        return folder
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / 'download.pending'
    for attempt in range(3):
        try:
            with urllib.request.urlopen(URLS[name], timeout=30) as response, archive.open('wb') as output:
                shutil.copyfileobj(response, output)
            break
        except OSError:
            if attempt == 2: raise
            time.sleep(2 ** attempt)
    if name == 'sparkle':
        with tarfile.open(archive) as package:
            package.extractall(folder, filter='data')
    else:
        with zipfile.ZipFile(archive) as package:
            for item in package.infolist():
                if not (folder / item.filename).resolve().is_relative_to(folder.resolve()):
                    raise ValueError('Invalid upstream archive path')
            package.extractall(folder)
    archive.unlink()
    marker.write_text(VERSIONS[name], encoding='ascii')
    return folder


if __name__ == '__main__':
    import sys
    print(fetch(sys.argv[1]))
