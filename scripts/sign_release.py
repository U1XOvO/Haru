"""Sign artifacts with upstream Ed25519 tools; private keys never enter output."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from fetch_release_tools import fetch, ROOT


def main():
    directory = ROOT / 'dist/installers'
    reports = list(directory.glob(('windows-' if sys.platform == 'win32' else 'macos-') + '*.json'))
    if len(reports) != 1: raise RuntimeError('Expected one platform report')
    report = json.loads(reports[0].read_text())
    if not report['updates_enabled']: raise RuntimeError('This build does not have an update public key')
    secret = os.environ['HARU_UPDATE_PRIVATE_KEY'].strip()
    package = directory / report['artifact']
    if sys.platform == 'win32':
        tool = next(fetch('winsparkle').rglob('winsparkle-tool.exe'))
        with tempfile.TemporaryDirectory() as temporary:
            key = Path(temporary) / 'key'
            key.write_text(secret, encoding='ascii');key.chmod(0o600)
            signature = subprocess.check_output([str(tool),'sign','--private-key-file',str(key),str(package)],text=True).strip()
        subprocess.run([str(tool),'verify','--public-key',report['public_key'],'--signature',signature,str(package)],check=True)
    else:
        tool = fetch('sparkle') / 'bin/sign_update'
        signature = subprocess.check_output([str(tool),'--ed-key-file','-','-p',str(package)],input=secret,text=True).strip()
        subprocess.run([str(tool),'--verify','--ed-key-file','-',str(package),signature],input=secret,text=True,check=True)
    if len(base64.b64decode(signature, validate=True)) != 64: raise ValueError('Invalid signature output')
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    Ed25519PublicKey.from_public_bytes(base64.b64decode(report['public_key'], validate=True)).verify(
        base64.b64decode(signature, validate=True), package.read_bytes())
    report.update(signature=signature, length=package.stat().st_size)
    reports[0].write_text(json.dumps(report,indent=2),encoding='utf-8')


if __name__ == '__main__': main()
