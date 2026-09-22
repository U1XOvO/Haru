"""Build standalone installers with separate stable and preview update channels."""
import argparse
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys

from app_config import bundle_info
from app_version import version
from fetch_release_tools import fetch
from release_feed import validate_release_tag

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/release'
OUTPUT = ROOT / 'dist/installers'


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], cwd=ROOT, check=True, **kwargs)


def require(*names):
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise RuntimeError('Missing release configuration: ' + ', '.join(missing))


def resources():
    """Only tracked UI and builtin data are eligible, never workspace-local files."""
    staging = WORK / 'resources'
    if staging.exists(): shutil.rmtree(staging)
    listing = run(['git', 'ls-files', '-z', '--', 'ui', 'data'], capture_output=True).stdout.decode().split('\0')
    for name in filter(None, listing):
        relative = Path(name)
        if any(part.startswith('.') for part in relative.parts) or relative.suffix not in {'.js','.css','.html','.png','.svg','.json'}:
            raise RuntimeError('Unexpected resource: ' + name)
        source = ROOT / relative
        if source.is_symlink(): raise RuntimeError('Release resources must be regular files')
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    for required in ('ui/index.html','ui/release.js','data/builtin_lessons.json', 'data/study/grammar.json','data/study/papers.json'):
        if not (staging / required).is_file():
            raise RuntimeError('Missing tracked release resource: ' + required)
    return staging


def metadata(signed, updates=False, channel=None):
    arch = 'arm64' if platform.machine().lower() in {'arm64','aarch64'} else 'x64'
    target = ('macos-' if sys.platform == 'darwin' else 'windows-') + arch
    updates = bool(updates or signed)
    if updates:
        require('HARU_UPDATE_PUBLIC_KEY')
        import base64
        if len(base64.b64decode(os.environ['HARU_UPDATE_PUBLIC_KEY'], validate=True)) != 32:
            raise ValueError('Expected an Ed25519 public key')
    channel = channel if channel is not None else ('stable' if signed else 'preview')
    app_version = version()
    tag = os.environ.get('HARU_RELEASE_TAG', 'v' + app_version + ('' if channel == 'stable' else '-preview'))
    validate_release_tag(app_version, channel, tag)
    result = dict(schema=1, version=app_version, distribution='release' if channel == 'stable' else 'preview', channel=channel,
                  platform=target, publisher_signed=bool(signed), updates_enabled=updates, release_tag=tag,
                  feed_url=f'https://u1xovo.github.io/Haru/updates/{channel}/{target}.xml',
                  public_key=os.environ.get('HARU_UPDATE_PUBLIC_KEY', '') if updates else '')
    path = WORK / 'app-release.json'
    path.write_text(json.dumps(result, indent=2), encoding='utf-8')
    return path, result


def sign_windows(path):
    require('HARU_WINDOWS_CERT_THUMBPRINT')
    run([os.environ.get('SIGNTOOL', 'signtool.exe'), 'sign', '/sha1',
         os.environ['HARU_WINDOWS_CERT_THUMBPRINT'], '/fd', 'SHA256',
         '/tr', 'http://timestamp.digicert.com', '/td', 'SHA256', path])


def notarize(path):
    result=run(['xcrun','notarytool','submit',path,'--key',os.environ['HARU_NOTARY_KEY'],
        '--key-id',os.environ['HARU_NOTARY_KEY_ID'],'--issuer',os.environ['HARU_NOTARY_ISSUER'],
        '--wait','--output-format','json'],capture_output=True,text=True)
    if json.loads(result.stdout).get('status') != 'Accepted':
        raise RuntimeError('Apple notarization did not accept the artifact')


def build_windows(signed, resource_dir, meta):
    from build_windows import build
    toolkit = fetch('winsparkle')
    candidates = [p for p in toolkit.rglob('WinSparkle.dll') if 'x64' in p.parts]
    if len(candidates) != 1: raise RuntimeError('Expected one upstream x64 WinSparkle DLL')
    os.environ['HARU_RELEASE_METADATA'] = str(meta)
    os.environ['HARU_RELEASE_RESOURCES'] = str(resource_dir)
    os.environ['HARU_WINSPARKLE_DLL'] = str(candidates[0])
    build(force=True)
    app = ROOT / 'dist/Haru'
    notices = app / 'licenses/WinSparkle'
    notices.mkdir(parents=True, exist_ok=True)
    for name in ('COPYING', 'COPYING.expat', 'AUTHORS'):
        shutil.copyfile(candidates[0].parents[2] / name, notices / name)
    if signed:
        sign_windows(app / 'Haru.exe')
        sign_windows(app / 'HaruBackend.exe')
    compiler = os.environ.get('ISCC') or shutil.which('ISCC.exe')
    if not compiler:
        compiler = str(Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Inno Setup 6/ISCC.exe')
    command = [compiler, '/DAppVersion=' + version(), '/DSourceDir=' + str(app), '/DOutputDir=' + str(OUTPUT)]
    if signed:
        # Inno also signs the uninstaller; do not publish an unsigned replacement path.
        require('HARU_WINDOWS_CERT_THUMBPRINT')
        sign_command = f'"{os.environ.get("SIGNTOOL", "signtool.exe")}" sign /sha1 {os.environ["HARU_WINDOWS_CERT_THUMBPRINT"]} /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $f'
        command += ['/DSignTool=haru', '/Sharu=' + sign_command]
    run(command + [ROOT / 'scripts/windows_installer.iss'])
    return OUTPUT / f'Haru-{version()}-windows-x64-setup.exe'


def build_macos(signed, resource_dir, meta):
    toolkit = fetch('sparkle')
    app = WORK / 'Haru.app'
    if app.exists(): shutil.rmtree(app)
    contents = app / 'Contents'
    assets = contents / 'Resources'
    (contents / 'MacOS').mkdir(parents=True)
    assets.mkdir()
    run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--distpath', WORK / 'python',
         '--workpath', WORK / 'pyinstaller', ROOT / 'scripts/haru_macos_backend.spec'])
    shutil.copytree(WORK / 'python/HaruBackend', assets / 'backend', symlinks=True)
    shutil.copytree(resource_dir / 'ui', assets / 'ui')
    shutil.copytree(resource_dir / 'data', assets / 'data')
    shutil.copyfile(meta, assets / 'app-release.json')
    shutil.copytree(toolkit / 'Sparkle.framework', contents / 'Frameworks/Sparkle.framework', symlinks=True)
    (assets / 'licenses').mkdir()
    shutil.copyfile(toolkit / 'LICENSE', assets / 'licenses/Sparkle.txt')
    # Build icons from the tracked drawing without changing tracked UI resources.
    icons = WORK / 'Haru.iconset'
    icons.mkdir(exist_ok=True)
    run(['xcrun', 'swift', '-module-cache-path', ROOT / 'build/swift-cache', ROOT / 'scripts/make_icon.swift', WORK / 'icon.png'])
    for size in (16,32,128,256,512):
        for scale, suffix in ((1,''),(2,'@2x')):
            run(['sips','-z',str(size*scale),str(size*scale),WORK/'icon.png','--out',icons/f'icon_{size}x{size}{suffix}.png'], stdout=subprocess.DEVNULL)
    run(['iconutil','-c','icns',icons,'-o',assets/'Haru.icns'])
    info = bundle_info(version())
    settings = json.loads(meta.read_text())
    info.update(SUFeedURL=settings['feed_url'], SUPublicEDKey=settings['public_key'],
                SUEnableAutomaticChecks=False, SUAllowsAutomaticUpdates=False)
    (contents/'Info.plist').write_bytes(plistlib.dumps(info))
    run(['xcrun','swiftc',ROOT/'native/Haru.swift','-swift-version','5','-O','-D','HARU_RELEASE',
         '-target', ('arm64' if platform.machine() == 'arm64' else 'x86_64') + '-apple-macos14.0',
         '-module-cache-path',ROOT/'build/swift-cache','-F',contents/'Frameworks','-framework','Sparkle',
         '-Xlinker','-rpath','-Xlinker','@executable_path/../Frameworks',
         '-framework','AppKit','-framework','WebKit','-framework','AVFoundation',
         '-o',contents/'MacOS/Haru'])
    identity = os.environ.get('HARU_MACOS_SIGN_IDENTITY', '-') if signed else '-'
    if signed: require('HARU_MACOS_SIGN_IDENTITY', 'HARU_NOTARY_KEY', 'HARU_NOTARY_KEY_ID', 'HARU_NOTARY_ISSUER')
    sign = ['codesign','--force','--sign',identity]
    if signed: sign += ['--timestamp','--options','runtime']
    # Sign every embedded Mach-O first, then bundles, then the outer application.
    for path in sorted(contents.rglob('*'), key=lambda p:len(p.parts), reverse=True):
        if path.is_symlink(): continue
        if path.is_file():
            with path.open('rb') as source: magic=source.read(4)
            if magic in (b'\xcf\xfa\xed\xfe',b'\xfe\xed\xfa\xcf',b'\xca\xfe\xba\xbe',b'\xbe\xba\xfe\xca'):
                run(sign+[path])
        elif path.suffix in {'.framework','.app','.xpc','.bundle'}:
            run(sign+[path])
    run(sign+[app])
    run(['codesign','--verify','--deep','--strict',app])
    if signed:
        archive=WORK/'notarization.zip'
        run(['ditto','-c','-k','--keepParent',app,archive])
        notarize(archive)
        run(['xcrun','stapler','staple',app])
    arch = 'arm64' if platform.machine() == 'arm64' else 'x64'
    dmg = OUTPUT / f'Haru-{version()}-macos-{arch}.dmg'
    image_root = WORK / 'dmg'
    if image_root.exists(): shutil.rmtree(image_root)
    image_root.mkdir()
    shutil.copytree(app,image_root/'Haru.app',symlinks=True)
    (image_root/'Applications').symlink_to('/Applications')
    run(['hdiutil','create','-ov','-volname','Haru','-srcfolder',image_root,'-format','UDZO',dmg])
    if signed:
        run(sign+[dmg])
        notarize(dmg)
        run(['xcrun','stapler','staple',dmg])
        run(['xcrun','stapler','validate',dmg])
    return dmg


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--signed',action='store_true',help='Require OS publisher signing credentials and enable verified updates')
    parser.add_argument('--updates',action='store_true',help='Enable Ed25519-verified updates without requiring publisher certificates')
    parser.add_argument('--channel',choices=('stable','preview'),help='Release channel (default: stable with --signed, otherwise preview)')
    args=parser.parse_args()
    if sys.platform not in {'win32','darwin'}: parser.error('Build on Windows or macOS')
    WORK.mkdir(parents=True,exist_ok=True);OUTPUT.mkdir(parents=True,exist_ok=True)
    if args.signed or args.channel == 'stable':
        if run(['git','status','--porcelain'],capture_output=True).stdout.strip():
            raise RuntimeError('Stable or publisher-signed releases require a clean checkout')
    meta,info=metadata(args.signed,args.updates,args.channel)
    resource_dir=resources()
    artifact=(build_windows if sys.platform=='win32' else build_macos)(args.signed,resource_dir,meta)
    report=dict(info,artifact=artifact.name)
    (OUTPUT/(info['platform']+'.json')).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('Built '+str(artifact))


if __name__=='__main__': main()
