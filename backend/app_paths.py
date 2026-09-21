"""Read-only bundle resources and persistent application storage."""
import os
from pathlib import Path
import sys


def resource_root():
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def storage_root():
    if not getattr(sys, 'frozen', False):
        return resource_root()
    # A locally built dist/Haru app keeps using this checkout's existing data.
    app = Path(sys.executable).resolve().parent
    project = app.parent.parent
    if (app.name == 'Haru' and app.parent.name == 'dist'
            and (project / 'start.cmd').is_file() and (project / 'pyproject.toml').is_file()):
        return project
    # Distributed bundles keep user data outside the replaceable app directory.
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'Haru'
