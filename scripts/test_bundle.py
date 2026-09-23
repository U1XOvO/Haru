"""Exercise shipped IPC from a relocated directory with an isolated learner store."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    parser=argparse.ArgumentParser();parser.add_argument('bundle',type=Path);parser.add_argument('--gui',action='store_true')
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='haru-distribution-test-') as temporary:
        root=Path(temporary)
        bundle=root/args.bundle.name
        shutil.copytree(args.bundle,bundle,symlinks=True)
        env=dict(os.environ,HARU_STORAGE_DIR=str(root/'storage'),HARU_DATA_DIR=str(root/'storage/runtime'))
        windows=sys.platform=='win32'
        worker=bundle/'HaruBackend.exe' if windows else bundle/'Contents/Resources/backend/HaruBackend'
        def rpc(action,params=None,expected_error=None):
            result=subprocess.run([str(worker)],input=json.dumps({'action':action,'params':params or {}}),
                text=True,encoding='utf-8',capture_output=True,cwd=root,env=env,timeout=40,check=True)
            result=json.loads(result.stdout)
            if expected_error is not None:
                if result != {'ok':False,'error':expected_error}: raise RuntimeError(result)
                return result
            if not result.get('ok'): raise RuntimeError(result)
            return result['data']
        # Empty text stops in shared-engine normalization before cache or network
        # access. Its exact error proves the frozen worker imported speech and
        # edge_tts; a missing bundled module would produce the generic IPC error.
        rpc('speech_prepare',{'text':''},expected_error='朗读文本无效，请使用 1 至 6000 个字符。')
        assert not (root/'storage/runtime/haru.sqlite3').exists()
        assert len(rpc('card_seed'))==5
        assert len(rpc('cards'))==5
        assert rpc('config_get')['key_configured'] is False
        assert rpc('prepare_update')['ready'] is True
        # Another process must read the same database after an application upgrade.
        assert len(rpc('cards'))==5
        if args.gui:
            report=root/'gui-report.json'
            app=bundle/'Haru.exe' if windows else bundle/'Contents/MacOS/Haru'
            subprocess.run([str(app),'--smoke-test',str(report)],env=env,cwd=root,timeout=90,check=True)
            if json.loads(report.read_text()).get('ok') is not True: raise RuntimeError('GUI smoke failed')
        print('Relocated bundle: offline Edge TTS loading, IPC, persistence, empty credentials and update backup passed'+('; GUI passed' if args.gui else ''))


if __name__=='__main__': main()
