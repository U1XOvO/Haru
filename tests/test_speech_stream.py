"""Real httpx SSE decoding with a fake Google endpoint; no network or billing."""
import asyncio
import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import speech_google
import speech_config
from speech import SpeechAudio


class SpeechStreamTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.data=self.root/'runtime';self.data.mkdir()
        config=speech_config.defaults();config.update(engine='gemini',key='private-fixture')
        speech_config.save_settings(config,self.root)
        config=speech_config.read_settings(self.root);config['voices'][0]['voice_id']='voice_fixture'
        speech_config._atomic_save(self.root,config)

    async def run_case(self, *, completed=True, cancel=False):
        pcm=b'\x01\x00'*24000;seen=[];calls=[];finished=[];edge_calls=[]
        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                for raw in (pcm[:3],pcm[3:]):
                    event={'event_type':'step.delta','delta':{'type':'audio','data':base64.b64encode(raw).decode()}}
                    data=('data: '+json.dumps(event)+'\r\n\r\n').encode()
                    for offset in range(0,len(data),71): yield data[offset:offset+71]
                if completed:
                    finished.append(True)
                    yield b'data: {"event_type":"interaction.completed","interaction":{"status":"completed"}}\n\n'
        async def respond(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Body())
        real=httpx.AsyncClient
        def client(**kw): return real(**kw,transport=httpx.MockTransport(respond))
        def on_audio(phase,raw):
            seen.append((phase,raw,bool(finished)))
            if phase=='chunk' and cancel: raise asyncio.CancelledError()
        data_dir=self.data
        class Edge:
            async def prepare(self,params,*,cancelled):
                edge_calls.append(True);p=data_dir/'edge.mp3';p.write_bytes(b'fixture')
                return SpeechAudio(p,False)
        with patch.object(speech_google.httpx,'AsyncClient',side_effect=client):
            params=dict(text='日本語の練習です。',rate=.42)
            try:
                result=await speech_google.prepare_speech(params,self.data,config_dir=self.root,edge_engine=Edge(),on_audio=on_audio)
            except asyncio.CancelledError:
                self.assertFalse(edge_calls)
                self.assertFalse(list(self.data.rglob('*.pending.*')))
                self.assertFalse(list(self.data.rglob('*.wav')))
                return
            if completed:
                self.assertEqual(result.engine,'gemini');self.assertTrue(result.streamed)
                self.assertTrue(any(phase=='chunk' and not done for phase,_,done in seen))
                self.assertEqual(b''.join(raw for phase,raw,_ in seen if phase=='chunk'),pcm)
                self.assertTrue(all(len(raw)<=24000 for _,raw,_ in seen))
                with wave.open(str(result.path),'rb') as audio:
                    self.assertEqual(audio.getframerate(),24000);self.assertEqual(audio.readframes(24000),pcm)
                seen.clear()
                cached=await speech_google.prepare_speech(params,self.data,config_dir=self.root,edge_engine=Edge(),on_audio=on_audio)
                self.assertFalse(cached.streamed);self.assertFalse(seen);self.assertEqual(len(calls),1)
                self.assertTrue(calls[0]['stream']);self.assertEqual(calls[0]['response_format']['mime_type'],'audio/l16')
            else:
                self.assertEqual(result.engine,'edge');self.assertTrue(result.fallback)
                self.assertEqual(seen[-1][0],'reset');self.assertEqual(len(edge_calls),1)
                self.assertFalse(list(self.data.rglob('*.wav')))
            self.assertFalse(list(self.data.rglob('*.pending.*')))

    def test_audio_plays_before_completion_and_reuses_complete_cache(self): asyncio.run(self.run_case())
    def test_incomplete_stream_resets_playback_and_is_not_cached(self): asyncio.run(self.run_case(completed=False))
    def test_cancel_after_first_sound_cleans_pending_without_fallback(self): asyncio.run(self.run_case(cancel=True))


if __name__=='__main__': unittest.main()
