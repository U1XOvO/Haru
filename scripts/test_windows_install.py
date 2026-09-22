"""Windows-only install/repair/uninstall acceptance in isolated directories."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

from app_version import version

ROOT=Path(__file__).resolve().parents[1]


def main():
    installer=ROOT/f'dist/installers/Haru-{version()}-windows-x64-setup.exe'
    with tempfile.TemporaryDirectory(prefix='haru-installer-test-') as temporary:
        root=Path(temporary);app=root/'installation';storage=root/'learner'
        env=dict(os.environ,HARU_STORAGE_DIR=str(storage),HARU_DATA_DIR=str(storage/'runtime'))
        command=[str(installer),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-','/DIR='+str(app)]
        subprocess.run(command,env=env,check=True,timeout=120)
        worker=app/'versions'/version()/'HaruBackend.exe'
        def request(action):
            result=subprocess.run([str(worker)],input=json.dumps({'action':action,'params':{}}),
                encoding='utf-8',text=True,capture_output=True,check=True,env=env,timeout=30)
            payload=json.loads(result.stdout)
            assert payload['ok'],payload
            return payload['data']
        assert len(request('card_seed'))==5
        # Repair/reinstall must leave the external learning database intact.
        subprocess.run(command,env=env,check=True,timeout=120)
        assert len(request('cards'))==5
        uninstall=next(app.glob('unins*.exe'))
        subprocess.run([str(uninstall),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'],check=True,timeout=120)
        assert (storage/'runtime/haru.sqlite3').is_file()
        print('Installer: install, repair, persistence and uninstall retention passed')


if __name__=='__main__': main()
