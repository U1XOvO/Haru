"""Read-only bundle resources and persistent application storage."""
import os
import json
from pathlib import Path
import sys


def resource_root():
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def storage_root():
    if os.environ.get('HARU_STORAGE_DIR'):
        return Path(os.environ['HARU_STORAGE_DIR']).expanduser().resolve()
    if not getattr(sys, 'frozen', False):
        return resource_root()
    # A locally built dist/Haru app keeps using this checkout's existing data.
    app = Path(sys.executable).resolve().parent
    project = app.parent.parent
    if (not (resource_root() / 'app-release.json').is_file()
            and app.name == 'Haru' and app.parent.name == 'dist'
            and (project / 'start.cmd').is_file() and (project / 'pyproject.toml').is_file()):
        return project
    # Distributed bundles keep user data outside the replaceable app directory.
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/Haru'
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'Haru'


def app_info():
    path = resource_root() / 'app-release.json'
    if path.is_file():
        info = json.loads(path.read_text(encoding='utf-8'))
        return {key: info[key] for key in ('version', 'distribution', 'updates_enabled')}
    import tomllib
    return dict(version=tomllib.loads((resource_root() / 'pyproject.toml').read_text())['project']['version'],
                distribution='source', updates_enabled=False)
