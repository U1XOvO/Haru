"""Ephemeral CI signing setup. Do not print secrets or failing command arguments."""
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'build/signing'


def quiet(command):
    result=subprocess.run(command,text=True,capture_output=True)
    if result.returncode: raise RuntimeError('Signing credential operation failed: '+Path(command[0]).name)
    return result.stdout.strip()


def export(name,value):
    if '\n' in value or '\r' in value: raise ValueError('Invalid signing configuration')
    with Path(os.environ['GITHUB_ENV']).open('a',encoding='utf-8') as output: output.write(name+'='+value+'\n')


def main():
    cleanup=sys.argv[1:]==['cleanup']
    if cleanup:
        if sys.platform=='darwin' and (FOLDER/'build.keychain-db').exists():
            quiet(['security','delete-keychain',str(FOLDER/'build.keychain-db')])
        if FOLDER.exists():
            import shutil
            shutil.rmtree(FOLDER)
        return
    FOLDER.mkdir(parents=True,exist_ok=True)
    if sys.platform=='darwin':
        cert=FOLDER/'certificate.p12'
        cert.write_bytes(base64.b64decode(os.environ['MACOS_CERTIFICATE_P12'],validate=True));cert.chmod(0o600)
        keychain=FOLDER/'build.keychain-db';password=secrets.token_urlsafe(24)
        quiet(['security','create-keychain','-p',password,str(keychain)])
        quiet(['security','set-keychain-settings','-lut','21600',str(keychain)])
        quiet(['security','unlock-keychain','-p',password,str(keychain)])
        quiet(['security','import',str(cert),'-k',str(keychain),'-P',os.environ['MACOS_CERTIFICATE_PASSWORD'],'-T','/usr/bin/codesign','-T','/usr/bin/security'])
        quiet(['security','set-key-partition-list','-S','apple-tool:,apple:','-s','-k',password,str(keychain)])
        quiet(['security','list-keychains','-d','user','-s',str(keychain)])
        key=FOLDER/'notary.p8';key.write_text(os.environ['APPLE_API_KEY_P8']);key.chmod(0o600)
        export('HARU_NOTARY_KEY',str(key));export('HARU_NOTARY_KEY_ID',os.environ['APPLE_API_KEY_ID'])
        export('HARU_NOTARY_ISSUER',os.environ['APPLE_API_ISSUER'])
        export('HARU_MACOS_SIGN_IDENTITY',os.environ['MACOS_SIGN_IDENTITY'])
    else:
        cert=FOLDER/'certificate.pfx'
        cert.write_bytes(base64.b64decode(os.environ['WINDOWS_CERTIFICATE_PFX'],validate=True));cert.chmod(0o600)
        os.environ['HARU_PFX_PATH']=str(cert)
        thumbprint=quiet(['powershell','-NoProfile','-Command',
            "$ErrorActionPreference='Stop'; $password=ConvertTo-SecureString $env:WINDOWS_CERTIFICATE_PASSWORD -AsPlainText -Force; (Import-PfxCertificate -FilePath $env:HARU_PFX_PATH -CertStoreLocation Cert:\\CurrentUser\\My -Password $password).Thumbprint"])
        export('HARU_WINDOWS_CERT_THUMBPRINT',thumbprint)
        sdks=Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))/'Windows Kits/10/bin'
        tools=sorted(sdks.glob('*/x64/signtool.exe'))
        if not tools: raise RuntimeError('Windows SDK SignTool is required')
        export('SIGNTOOL',str(tools[-1]))


if __name__=='__main__': main()
