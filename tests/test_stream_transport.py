"""Progress crosses the real subprocess boundary before final completion."""
import ctypes as C
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'native'),str(ROOT/'backend')]
from desktop_bridge import Backend
from windows_pcm import PCMPlayer, WaveHeader


class TransportTests(unittest.TestCase):
    def test_progress_is_forwarded_before_worker_can_finish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);script=root/'bridge.py';ack=root/'ack'
            script.write_text('''import json,sys,time
from pathlib import Path
request=json.load(sys.stdin)
assert request['stream'] is True
print(json.dumps({'event':'generation','phase':'content','preview':{'word':'猫'}}),flush=True)
ack=Path(__file__).with_name('ack')
end=time.monotonic()+3
while not ack.exists() and time.monotonic()<end: time.sleep(.01)
print(json.dumps({'ok':ack.exists(),'data':{'saved':True}}),flush=True)
''')
            backend=Backend(root,bridge=script,deadline=5);events=[]
            def progress(event): events.append(event);ack.touch()
            try:
                result=backend.request({'id':1,'action':'card_create','params':{'word':'猫'}},on_progress=progress)
            finally: backend.close()
            self.assertTrue(result['ok']);self.assertEqual(events[0]['preview']['word'],'猫')

    def test_windows_pcm_owns_buffers_and_preserves_pause_stop(self):
        api=Mock();headers=[]
        for name in ('waveOutOpen','waveOutPrepareHeader','waveOutWrite','waveOutUnprepareHeader','waveOutPause','waveOutRestart','waveOutReset','waveOutClose'):
            getattr(api,name).return_value=0
        def write(handle,ptr,size):
            headers.append(C.cast(ptr,C.POINTER(WaveHeader)).contents)
            return 0
        def reset(handle):
            for h in headers: h.flags |= 1
            return 0
        api.waveOutWrite.side_effect=write;api.waveOutReset.side_effect=reset
        player=PCMPlayer(api);player.append(b'\x01\x00'*100)
        self.assertEqual(player.state,'playing')
        self.assertEqual(C.string_at(headers[0].data,headers[0].length),b'\x01\x00'*100)
        player.toggle_pause();self.assertEqual(player.state,'paused')
        player.append(b'\x02\x00'*50);self.assertEqual(player.state,'paused')
        player.toggle_pause();player.finish();self.assertEqual(player.state,'playing')
        for h in headers:h.flags |= 1
        self.assertEqual(player.state,'idle');self.assertFalse(player.buffers)
        player.stop();player.stop();api.waveOutClose.assert_called_once()
        with self.assertRaises(OSError):player.append(b'\0\0')


if __name__=='__main__':unittest.main()
