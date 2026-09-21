"""Verify a built Windows bundle in isolation; never call an AI provider."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def subsystem(path):
    """Read the PE optional header's Windows subsystem field."""
    with path.open('rb') as source:
        source.seek(0x3c)
        pe_offset = int.from_bytes(source.read(4), 'little')
        source.seek(pe_offset)
        assert source.read(4) == b'PE\0\0', path
        source.seek(pe_offset + 24 + 68)
        return int.from_bytes(source.read(2), 'little')


def main():
    if sys.platform != 'win32':
        raise SystemExit('This test requires Windows and a built dist/Haru bundle.')
    with tempfile.TemporaryDirectory(prefix='haru 独立 app ') as directory:
        root = Path(directory)
        # Moving the app away from the checkout verifies it is self-contained.
        app = root / 'Haru'
        shutil.copytree(ROOT / 'dist/Haru', app)
        assert subsystem(app / 'Haru.exe') == 2, 'GUI must use Windows subsystem'
        assert subsystem(app / 'HaruBackend.exe') == 3, 'IPC must retain standard pipes'
        assert not list(app.rglob('.env')), 'Private configuration was bundled'
        assert not list(app.rglob('*.sqlite3')), 'Learning data was bundled'
        env = dict(os.environ, LOCALAPPDATA=str(root / 'local'), HARU_DATA_DIR=str(root / 'learning'))
        env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
        for key in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV'):
            env.pop(key, None)
        def request(action, params=None):
            reply = subprocess.run([str(app / 'HaruBackend.exe')], cwd=root, env=env,
                input=json.dumps({'action': action, 'params': params or {}}).encode('utf-8'),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW, check=True)
            data = json.loads(reply.stdout.decode('utf-8'))
            assert data['ok'], data
            return data['data']
        cards = request('card_seed')
        assert cards and any(card.get('word') == '水' for card in cards)
        assert request('cards') == cards
        assert request('grammar_catalog')
        assert request('study_catalog')
        request('config_save', {'base': 'https://offline.invalid/v1', 'model': 'fixture', 'key': 'fixture'})
        assert (root / 'local/Haru/.env').is_file()
        assert request('config_get')['model'] == 'fixture'
        env.pop('HARU_DATA_DIR')
        assert request('card_seed')
        assert (root / 'local/Haru/runtime/haru.sqlite3').is_file()
        report = root / 'smoke.json'
        process = subprocess.run([str(app / 'Haru.exe'), '--smoke-test', str(report)],
                                 cwd=root, env=env, timeout=60)
        assert process.returncode == 0, process.returncode
        assert json.loads(report.read_text(encoding='utf-8'))['ok']
        print('PASS: standalone GUI, WebView2 IPC, Unicode, persistence, resources, no bundled user data.')


if __name__ == '__main__':
    main()
