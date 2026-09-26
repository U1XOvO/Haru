"""Offline request policy, incremental output, and incomplete-stream checks."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import httpx
import openai

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import generation
import llm
import llm_config


class GenerationTests(unittest.TestCase):
    def config(self, **values):
        return dict(llm_config.profile_defaults(), model='fixture', key='private-key',
                    base='https://example.test/v1', timeout=10) | values

    def test_task_policy_and_explicit_opt_out(self):
        for schema in ({'word': ''}, {'cards': []}, {'tokens': []}, {'ok': True}):
            self.assertEqual(llm.build_payload(self.config(reasoning='high'), '', {}, schema)['reasoning_effort'], 'none')
        for role, expected in [('author', 'low'), ('reviewer', 'high'), ('global_reviewer', 'high'), ('explanation_editor', 'low')]:
            self.assertEqual(llm.build_payload(self.config(), '', {'_jlpt_role': role}, {})['reasoning_effort'], expected)
        self.assertNotIn('reasoning_effort', llm.build_payload(self.config(task_reasoning=False), '', {}, {}))
        row=llm_config.normalize_profile(dict(self.config(),id='a',name='a'))
        self.assertTrue(row['task_reasoning'])
        with self.assertRaises(llm.AppError):
            llm_config.normalize_profile(dict(row,task_reasoning='yes'))

    def test_incomplete_json_only_exposes_complete_safe_values(self):
        raw='{"translation":"日本語\\n测试", "sentences":[{"jp":"こんにちは","zh":"你好"}, {"jp":"まだ'
        self.assertEqual(generation.preview_fields(raw), {'translation':'日本語\n测试', 'sentences':[{'jp':'こんにちは','zh':'你好'}]})
        raw='{"questions":[{"answer":1,"explanation":"secret"}],"title":"测验","reasoning_content":"private"}'
        self.assertEqual(generation.preview_fields(raw), {'title':'测验'})
        self.assertEqual(generation.preview_fields('{"word":"未完成'), {})

    def request(self, pieces, finish='stop'):
        observed=[]; done=[]; requests=[]
        class Body(httpx.SyncByteStream):
            def __iter__(self):
                for piece in pieces:
                    obj={'id':'test','object':'chat.completion.chunk','created':0,'model':'fixture',
                         'choices':[{'index':0,'delta':{'content':piece,'reasoning_content':'hidden-reasoning'},'finish_reason':None}]}
                    yield ('data: '+json.dumps(obj)+'\n\n').encode()
                if finish is not None:
                    yield ('data: '+json.dumps({'id':'test','object':'chat.completion.chunk','created':0,'model':'fixture',
                        'choices':[{'index':0,'delta':{},'finish_reason':finish}]})+'\n\n').encode()
                done.append(True)
                yield b'data: [DONE]\n\n'
        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Body())
        original=openai.OpenAI
        def client(**kw):
            kw['http_client'].close()
            kw['http_client']=httpx.Client(transport=httpx.MockTransport(respond))
            return original(**kw)
        def progress(event): observed.append((event, bool(done)))
        with patch.object(llm,'configuration',return_value=self.config()), patch.object(openai,'OpenAI',side_effect=client), generation.progress_scope('decode',progress):
            result=llm.generate('fixture',{}, {'translation':''})
        return result,observed,requests

    def test_preview_precedes_completion_and_hides_reasoning(self):
        result,events,requests=self.request(['{"translation":"你好",', '"questions":[{"answer":1}],"reading":"こんにちは"}'])
        self.assertTrue(requests[0]['stream'])
        self.assertTrue(any(e.get('preview',{}).get('translation')=='你好' and not done for e,done in events))
        self.assertNotIn('hidden-reasoning',json.dumps(events))
        self.assertNotIn('answer',json.dumps(events))
        self.assertEqual(result[0]['reading'],'こんにちは')
        self.assertFalse(generation.streaming_text())

    def test_interruption_truncation_and_bad_json_never_return_success(self):
        for pieces,finish in [(['{"translation":"你好"}'],None), (['{"translation":"你好"}'],'length'), (['{"translation":"你好",'], 'stop')]:
            with self.subTest(finish=finish), self.assertRaises(llm.AppError):
                self.request(pieces,finish)


if __name__ == '__main__': unittest.main()
