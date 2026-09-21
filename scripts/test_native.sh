#!/bin/bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
bash "$PROJECT_DIR/scripts/build_app.sh" --if-needed
QA_DIR="$PROJECT_DIR/build/HaruQA.app/Contents"
mkdir -p "$QA_DIR/MacOS" "$QA_DIR/Resources" "$PROJECT_DIR/runtime/validation"
"$PROJECT_DIR/.venv/bin/python" - "$PROJECT_DIR" <<'PY'
import pathlib,sys,plistlib
r=pathlib.Path(sys.argv[1]); source=(r/'native/Haru.swift').read_text();harness=(r/'tests/native_smoke.swift').read_text()
source=source.replace('let app=NSApplication.shared',harness+'\nlet app=NSApplication.shared').replace('app.run()','startQA(delegate)\napp.run()')
source=source.replace('backend/bridge.py','tests/native_fixture_bridge.py')
(r/'build/qa_main.swift').write_text(source)
info=plistlib.loads((r/'Haru.app/Contents/Info.plist').read_bytes());info['CFBundleIdentifier']='local.haru.japanese.qa';info['CFBundleExecutable']='HaruQA';info['CFBundleName']='HaruQA'
(r/'build/HaruQA.app/Contents/Info.plist').write_bytes(plistlib.dumps(info))
(r/'build/HaruQA.app/Contents/Resources/runtime.json').write_bytes((r/'Haru.app/Contents/Resources/runtime.json').read_bytes())
PY
xcrun swiftc "$PROJECT_DIR/build/qa_main.swift" -swift-version 5 -module-cache-path "$PROJECT_DIR/build/swift-cache" -framework AppKit -framework WebKit -framework AVFoundation -o "$QA_DIR/MacOS/HaruQA"
cp "$PROJECT_DIR/Haru.app/Contents/Resources/Haru.icns" "$QA_DIR/Resources/Haru.icns"
codesign --force --sign - "$PROJECT_DIR/build/HaruQA.app"
QA_DATA="$(mktemp -d "$PROJECT_DIR/runtime/validation/native-db-XXXXXX")"
"$PROJECT_DIR/.venv/bin/python" - "$QA_DATA" <<'PY'
import pathlib,sys,wave
folder=pathlib.Path(sys.argv[1])/'study-assets';folder.mkdir()
with wave.open(str(folder/'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.wav'),'wb') as out:
    out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(b'\x00\x00'*1600)
PY
HARU_DATA_DIR="$QA_DATA" HARU_QA_RESULT="$PROJECT_DIR/runtime/validation/native-result.json" "$QA_DIR/MacOS/HaruQA"
"$PROJECT_DIR/.venv/bin/python" - "$PROJECT_DIR/runtime/validation/native-result.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]));print(json.dumps(r,ensure_ascii=False,indent=2));assert r['ok'],r
PY
