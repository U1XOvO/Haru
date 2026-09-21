"""Relocate and test the actual macOS Release app without source or live AI."""
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
from release_utils import ROOT, version


def main():
    if sys.platform != 'darwin':
        raise SystemExit('This check requires macOS.')
    with tempfile.TemporaryDirectory(prefix='haru 独立 app ') as directory:
        root = Path(directory)
        app = root / 'Haru.app'
        shutil.copytree(ROOT / 'dist/Haru.app', app, symlinks=True)
        resources = app / 'Contents/Resources'
        assert json.loads((resources / 'runtime.json').read_text()) == {'mode': 'bundled'}
        assert plistlib.loads((app / 'Contents/Info.plist').read_bytes())['CFBundleShortVersionString'] == version()
        assert (resources / 'ui/index.html').is_file()
        assert not list(app.rglob('.env'))
        assert not list(app.rglob('*.sqlite3'))
        env = dict(os.environ, HOME=str(root / 'home'), HARU_DATA_DIR=str(root / 'learning'), PATH='/usr/bin:/bin')
        for key in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV'):
            env.pop(key, None)
        worker = resources / 'backend-runtime/HaruBackend'
        def request(action, params=None):
            reply = subprocess.run([str(worker)], cwd=root, env=env,
                input=json.dumps({'action': action, 'params': params or {}}).encode('utf-8'),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=True)
            data = json.loads(reply.stdout.decode('utf-8'))
            assert data['ok'], data
            return data['data']
        cards = request('card_seed')
        assert any(card.get('word') == '水' for card in cards)
        assert request('cards') == cards
        assert request('grammar_catalog') and request('study_catalog')
        assert request('curriculum')
        # Write only fixture credentials to an isolated HOME and verify persistence.
        request('config_save', {'base': 'https://offline.invalid/v1', 'model': 'fixture', 'key': 'fixture'})
        assert (root / 'home/Library/Application Support/Haru/.env').is_file()
        assert request('config_get')['model'] == 'fixture'
        env.pop('HARU_DATA_DIR')
        assert request('card_seed')
        assert (root / 'home/Library/Application Support/Haru/runtime/haru.sqlite3').is_file()
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
        report = root / 'smoke.json'
        subprocess.run([str(app / 'Contents/MacOS/Haru'), '--smoke-test', str(report)],
                       cwd=root, env=env, timeout=60, check=True)
        assert json.loads(report.read_text())['ok']
        print('PASS: relocated macOS app, native UI/IPC, bundled resources, persistence and signature.')


if __name__ == '__main__':
    main()
