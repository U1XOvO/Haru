"""Check upstream signers with disposable keys, including rejection of tampering."""
import base64
from pathlib import Path
import subprocess
import sys
import tempfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fetch_release_tools import fetch


def main():
    key = Ed25519PrivateKey.generate()
    private = base64.b64encode(key.private_bytes_raw()).decode('ascii')
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode('ascii')
    with tempfile.TemporaryDirectory(prefix='haru-signature-test-') as temporary:
        root = Path(temporary)
        payload = root / 'sample.bin'; payload.write_bytes(b'Haru offline signature acceptance')
        if sys.platform == 'win32':
            tool = next(fetch('winsparkle').rglob('winsparkle-tool.exe'))
            key_file = root / 'key'; key_file.write_text(private, encoding='ascii')
            signature = subprocess.check_output([str(tool), 'sign', '--private-key-file', str(key_file), str(payload)], text=True).strip()
            verify = [str(tool), 'verify', '--public-key', public, '--signature', signature, str(payload)]
            stdin = None
        else:
            tool = fetch('sparkle') / 'bin/sign_update'
            signature = subprocess.check_output([str(tool), '--ed-key-file', '-', '-p', str(payload)], input=private, text=True).strip()
            verify = [str(tool), '--verify', '--ed-key-file', '-', str(payload), signature]
            stdin = private
        key.public_key().verify(base64.b64decode(signature, validate=True), payload.read_bytes())
        subprocess.run(verify, input=stdin, text=True, capture_output=True, check=True)
        payload.write_bytes(b'tampered')
        assert subprocess.run(verify, input=stdin, text=True, capture_output=True).returncode != 0
    print('Upstream signer: disposable key, independent verification and tamper rejection passed')


if __name__ == '__main__': main()
