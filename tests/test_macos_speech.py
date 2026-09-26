"""Offline macOS speech IPC regression using the actual Swift app class.

The harness never opens a window, records, or contacts a speech provider. Its
fake Python worker returns silent WAV bytes; playback failure is allowed on
machines without an audio output, but cancellation and cleanup must still pass.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
SWIFTC = shutil.which('swiftc')

FAKE_BRIDGE = r'''
import io
import base64
import json
import os
from pathlib import Path
import signal
import sys
import time
import wave

request = json.load(sys.stdin)
assert request['action'] == 'speech_prepare'
params = request['params']
case = params['text']
root = Path(os.environ['HARU_DATA_DIR'])
cache = root / ('tts-gemini-cache' if case in ('gemini-wav', 'stream-complete') else 'tts-cache')
cache.mkdir(exist_ok=True)
if case == 'stream':
    print(json.dumps({'event':'audio','phase':'chunk','pcm':base64.b64encode(b'\0\0'*12000).decode()}),flush=True)
    (root/'ready-stream').touch()
    time.sleep(.6)
    print(json.dumps({'ok':False,'error':'fixture stream interruption'}),flush=True)
    sys.exit(0)
if case == 'provider-error':
    result = {'ok': False, 'error': '此内容尚未缓存，请联网后重试。'}
elif case == 'traversal':
    result = {'ok': True, 'data': {'audio': '../outside.mp3', 'transient': True}}
else:
    name = f"{params['fixture_id']:032x}.{'wav' if case in ('gemini-wav', 'stream-complete') else 'mp3'}"
    output = cache / name
    if case == 'symlink':
        output.symlink_to(root / 'outside.mp3')
    else:
        stream = io.BytesIO()
        with wave.open(stream, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            wav.writeframes(b'\0\0' * 16000)
        output.write_bytes(stream.getvalue())
    # Exercise late *successful* results after stop, not only killed workers.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    (root / f'ready-{case}').touch()
    if case == 'stream-complete':
        print(json.dumps({'event':'audio','phase':'chunk','pcm':base64.b64encode(b'\0\0'*12000).decode()}),flush=True)
    time.sleep(params.get('delay', 0))
    result = {'ok': True, 'data': {'audio': name, 'transient': True,
                                  'engine': 'gemini' if case in ('gemini-wav', 'stream-complete') else 'edge',
                                  'fallback': '', 'streamed': case == 'stream-complete'}}
print(json.dumps(result, ensure_ascii=False), flush=True)
'''

SWIFT_MAIN = r'''
let fixtureRoot = URL(fileURLWithPath:CommandLine.arguments[1],isDirectory:true)
let subject = HaruApp()
subject.root = fixtureRoot
subject.python = CommandLine.arguments[2]
subject.storageRoot = fixtureRoot
subject.dataDir = fixtureRoot.appendingPathComponent("runtime",isDirectory:true)
try FileManager.default.createDirectory(at:subject.dataDir,withIntermediateDirectories:true)
let outside = subject.dataDir.appendingPathComponent("outside.mp3")
try Data("outside must remain unchanged".utf8).write(to:outside)
var results: [Int:[String:Any]] = [:]
var playbackAttempts = 0
var playbackSuccesses = 0
var streamSuccesses = 0

func expect(_ condition: @autoclosure () -> Bool,_ message:String) {
    if !condition() { fputs("FAIL: \(message)\n",stderr); exit(1) }
}
func pump(_ seconds:Double = 0.04) {
    let deadline = Date().addingTimeInterval(seconds)
    while Date() < deadline {
        _ = RunLoop.current.run(mode:.default,before:Date().addingTimeInterval(0.01))
    }
}
func until(_ message:String, timeout:Double = 5,_ condition:()->Bool) {
    let deadline = Date().addingTimeInterval(timeout)
    while !condition() && Date() < deadline { pump(0.01) }
    expect(condition(),message)
}
func start(_ id:Int,_ text:String,_ fixtureID:Int = 0,_ delay:Double = 0) {
    subject.maintenanceCompletions[id] = { result in
        expect(results[id] == nil,"reply \(id) must resolve once")
        results[id] = result
    }
    subject.startSpeech(id,["text":text,"rate":0.42,"fixture_id":fixtureID,"delay":delay])
}
func ready(_ text:String) {
    until("worker \(text) started") {
        FileManager.default.fileExists(atPath:subject.dataDir.appendingPathComponent("ready-"+text).path)
    }
}
func audio(_ fixtureID:Int)->URL {
    subject.dataDir.appendingPathComponent("tts-cache/"+String(format:"%032x",fixtureID)+".mp3")
}
func workersFinished() {
    until("workers finish") {
        subject.processLock.lock();defer {subject.processLock.unlock()}
        return subject.backendProcesses.isEmpty
    }
    pump()
}
func acceptsPlayback(_ id:Int) {
    until("playback reply \(id)") { results[id] != nil }
    playbackAttempts += 1
    if results[id]?["ok"] as? Bool == true {
        playbackSuccesses += 1
        expect(subject.player != nil,"successful playback owns player")
    } else {
        let error = results[id]?["error"] as? String ?? ""
        expect(error == "音频无法播放，请检查输出设备。" || error == "音频无法播放，请检查输出设备后重试。",
               "only missing audio output may prevent silent playback: \(error)")
    }
}
func togglePause(_ id:Int) -> String {
    subject.maintenanceCompletions[id] = { result in results[id] = result }
    subject.toggleAudioPause(id)
    return (results[id]?["data"] as? [String:String])?["state"] ?? ""
}

// Stop acknowledges the pending request synchronously, then discards a late result.
start(1,"cancel",1,0.35)
ready("cancel")
expect(results[1] == nil,"synthesis is still pending")
subject.stopAudio()
expect(results[1]?["ok"] as? Bool == true,"stop immediately resolves pending speech")
expect(subject.speechReplyID == nil && subject.player == nil,"stop clears playback state")
workersFinished()
expect(subject.player == nil,"cancelled success must never start playback")
expect(!FileManager.default.fileExists(atPath:audio(1).path),"late transient is deleted")

// An older callback must not clear the newest still-pending reply or play audio.
start(2,"old-pending",2,0.20)
ready("old-pending")
start(3,"new-pending",3,0.65)
ready("new-pending")
expect(results[2]?["ok"] as? Bool == true,"replacement resolves old request")
until("older result cleanup") { !FileManager.default.fileExists(atPath:audio(2).path) }
expect(subject.speechReplyID == 3 && results[3] == nil,"old callback preserves current reply")
expect(subject.player == nil,"old callback cannot play during new synthesis")
acceptsPlayback(3)
expect(!FileManager.default.fileExists(atPath:audio(3).path),"loaded transient is deleted")
if subject.player?.isPlaying == true {
    expect(togglePause(30) == "paused" && subject.audioPaused,"pause retains the active player")
    expect(subject.audioState() == "paused","paused player reports its state")
    expect(togglePause(31) == "playing" && !subject.audioPaused,"resume continues the player")
}
subject.stopAudio()
expect(subject.audioState() == "idle","stopped player reports idle")
expect(togglePause(32) == "idle","stopped audio cannot resume")
workersFinished()

// A late old success also must not overwrite a newer player already created.
start(4,"old-late",4,0.35)
ready("old-late")
start(5,"new-fast",5)
acceptsPlayback(5)
let latestPlayer = subject.player
workersFinished()
expect(subject.player === latestPlayer,"old result must preserve the latest player")
expect(!FileManager.default.fileExists(atPath:audio(4).path),"replaced transient is deleted")
subject.stopAudio()

start(6,"provider-error")
until("friendly provider error") { results[6] != nil }
expect(results[6]?["error"] as? String == "此内容尚未缓存，请联网后重试。","provider error passes through IPC")
expect(subject.player == nil,"provider failure never starts a fallback")
start(7,"traversal")
until("traversal rejected") { results[7] != nil }
expect(results[7]?["ok"] as? Bool == false,"parent paths are rejected")
start(8,"symlink",8)
until("symlink rejected") { results[8] != nil }
expect(results[8]?["ok"] as? Bool == false,"symlink audio is rejected")
expect((try? String(contentsOf:outside,encoding:.utf8)) == "outside must remain unchanged","invalid transient paths cannot delete external files")
workersFinished()

// Gemini WAV passes the same native file validation, playback and transient cleanup.
start(10,"gemini-wav",10)
acceptsPlayback(10)
if results[10]?["ok"] as? Bool == true {
    let data = results[10]?["data"] as? [String:Any] ?? [:]
    expect(data["engine"] as? String == "gemini","Gemini engine reaches the UI")
}
let geminiFile = subject.dataDir.appendingPathComponent("tts-gemini-cache/"+String(format:"%032x",10)+".wav")
expect(!FileManager.default.fileExists(atPath:geminiFile.path),"Gemini transient is deleted")
subject.stopAudio()
workersFinished()

// An audio event must reach the player before the slow worker finishes.
start(11,"stream",11)
until("stream starts or device rejects") { subject.pcmPlayer != nil || results[11] != nil }
if subject.pcmPlayer != nil {
    expect(results[11] == nil,"PCM playback precedes final backend result")
    expect(subject.audioState() == "playing","stream is playing during download")
    expect(togglePause(12) == "paused","stream pauses while download continues")
    expect(togglePause(13) == "playing","stream resumes without restarting")
    subject.stopAudio()
    expect(subject.pcmPlayer == nil,"stop releases stream player")
}
workersFinished()
expect(subject.pcmPlayer == nil,"late stream result cannot restart playback")

start(14,"stream-complete",14,0.6)
until("complete stream starts or device rejects") { subject.pcmPlayer != nil || results[14] != nil }
if subject.pcmPlayer != nil {
    expect(results[14] == nil,"complete PCM stream starts before final response")
    until("stream final result") { results[14] != nil }
    expect(results[14]?["ok"] as? Bool == true,"successful stream has a successful final response")
    streamSuccesses += 1
    expect(subject.player == nil,"cached WAV must not replay the streamed audio")
    expect(subject.pcmPlayer?.ended == true,"stream receives completion marker")
}
subject.stopAudio()
workersFinished()

// Shutdown invalidates queued speech and child processes without launching UI.
start(9,"shutdown",9,0.35)
ready("shutdown")
subject.applicationWillTerminate(Notification(name:Notification.Name("FixtureShutdown")))
expect(results[9]?["ok"] as? Bool == true,"shutdown resolves pending speech")
expect(subject.shuttingDown && subject.player == nil,"shutdown invalidates playback")
// Keep the main run loop blocked, as real application termination does. The
// background IPC completion must remove the transient without a UI callback.
let shutdownDeadline = Date().addingTimeInterval(5)
while Date() < shutdownDeadline {
    subject.processLock.lock()
    let done = subject.backendProcesses.isEmpty
    subject.processLock.unlock()
    if done { break }
    Thread.sleep(forTimeInterval:0.01)
}
expect(!FileManager.default.fileExists(atPath:audio(9).path),"shutdown cleanup cannot depend on main callbacks")
workersFinished()
expect(subject.player == nil,"shutdown completion never plays late audio")
expect(!FileManager.default.fileExists(atPath:audio(9).path),"shutdown transient is deleted")
print("macOS native IPC checks passed; silent playback attempts=\(playbackAttempts), successful=\(playbackSuccesses), completed PCM streams=\(streamSuccesses)")
'''


@unittest.skipUnless(sys.platform == 'darwin' and SWIFTC, 'requires macOS and swiftc')
class MacOSSpeechTests(unittest.TestCase):
    def test_actual_native_class_cancellation_paths_and_transient_cleanup(self):
        with tempfile.TemporaryDirectory(prefix='haru-macos-speech-') as temporary:
            folder = Path(temporary)
            backend = folder / 'backend'
            backend.mkdir()
            (backend / 'bridge.py').write_text(textwrap.dedent(FAKE_BRIDGE), encoding='utf-8')
            source = (ROOT / 'native/Haru.swift').read_text(encoding='utf-8')
            marker = 'let app=NSApplication.shared'
            self.assertEqual(source.count(marker), 1, 'native entry point changed; update harness boundary')
            main = folder / 'main.swift'
            main.write_text(source.split(marker)[0] + SWIFT_MAIN, encoding='utf-8')
            executable = folder / 'speech-harness'
            environment = dict(os.environ, CLANG_MODULE_CACHE_PATH=str(folder / 'module-cache'))
            compiled = subprocess.run(
                [SWIFTC, str(main), '-o', str(executable), '-module-cache-path', str(folder / 'module-cache')],
                capture_output=True, text=True, timeout=120, env=environment,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run(
                [str(executable), str(folder), sys.executable],
                capture_output=True, text=True, timeout=30, env=environment,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('macOS native IPC checks passed', result.stdout)
            print(result.stdout.strip())


if __name__ == '__main__':
    unittest.main()
