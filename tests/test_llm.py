"""Exercise the real OpenAI SDK against HTTPX's in-memory transport (no API fees)."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
import openai

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import llm


def completion(content='{"ok":true}', **choice):
    return {'model': 'actual-model', 'choices': [dict(
        {'finish_reason': 'stop', 'message': {'content': content}}, **choice)]}


class SDKTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(base='https://api.deepseek.com', key='test-secret',
                           model='configured-model', timeout=10)
        self.requests = []
        self.client_options = []
        self.responses = []
        self.enterContext(patch('llm.configuration', return_value=self.config))
        self.enterContext(patch('openai.DefaultHttpxClient', side_effect=self.client))
        self.enterContext(patch('openai._base_client.time.sleep'))

    def client(self, **options):
        self.client_options.append(options)
        return httpx.Client(transport=httpx.MockTransport(self.respond), **options)

    def respond(self, request):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def generate(self):
        return llm.generate('测试任务', {'sentence': 'こんにちは'}, {'ok': True})

    def test_sdk_payload_and_effective_model(self):
        self.responses = [httpx.Response(200, json=completion())]
        self.assertEqual(self.generate(), ({'ok': True}, 'actual-model'))
        request = self.requests[0]
        self.assertEqual(str(request.url), 'https://api.deepseek.com/chat/completions')
        self.assertEqual(request.headers['authorization'], 'Bearer test-secret')
        payload = json.loads(request.content)
        self.assertEqual(payload['model'], 'configured-model')
        self.assertEqual(payload['thinking'], {'type': 'enabled'})
        self.assertEqual(payload['reasoning_effort'], 'high')
        self.assertNotIn('temperature',payload)
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        self.assertEqual(payload['max_tokens'], 16000)
        self.assertEqual(json.loads(payload['messages'][1]['content'])['context'],
                         {'sentence': 'こんにちは'})
        self.assertFalse(self.client_options[0]['follow_redirects'])

    def test_full_endpoint_and_generic_provider(self):
        self.config['base'] = 'https://example.test/v1/chat/completions'
        self.responses = [httpx.Response(200, json=completion())]
        self.generate()
        self.assertEqual(str(self.requests[0].url), self.config['base'])
        self.assertNotIn('thinking', json.loads(self.requests[0].content))

    def test_rich_lesson_has_room_for_full_material_without_changing_other_requests(self):
        for base, budget in [('https://api.deepseek.com', 24000), ('https://example.test/v1', 10000)]:
            self.config['base'] = base
            self.responses = [httpx.Response(200, json=completion())]
            llm.generate('每日课程', {'lesson_design': {'version': 2}}, {'title': '课'})
            self.assertEqual(json.loads(self.requests[-1].content)['max_tokens'], budget)

    def test_jlpt_flash_uses_exam_system_and_bounded_reasoning(self):
        self.config['model']='deepseek-flash'
        body=completion();body['usage']={'prompt_tokens':123,'completion_tokens':456,'total_tokens':579}
        body['choices'][0]['message']['reasoning_content']='private intermediate reasoning'
        self.responses=[httpx.Response(200,json=body)]
        result,_=llm.generate('审题',{'_jlpt_role':'reviewer'},{'ok':True})
        payload=json.loads(self.requests[0].content)
        self.assertEqual(payload['thinking'],{'type':'enabled'})
        self.assertEqual(payload['reasoning_effort'],'high')
        self.assertEqual(payload['max_tokens'],16000)
        self.assertNotIn('temperature',payload)
        self.assertIn('不要附罗马音',payload['messages'][0]['content'])
        self.assertEqual(result['_generation_meta']['usage']['total_tokens'],579)
        self.assertNotIn('private intermediate',json.dumps(result))

    def test_exam_profile_does_not_assume_generic_provider_supports_deepseek_reasoning(self):
        self.config.update(base='https://example.test/v1',model='deepseek-flash')
        self.responses=[httpx.Response(200,json=completion())]
        llm.generate('命题',{'_jlpt_role':'author'},{'ok':True})
        payload=json.loads(self.requests[0].content)
        self.assertNotIn('thinking',payload)
        self.assertNotIn('reasoning_effort',payload)
        self.assertEqual(payload['temperature'],0.35)

    def test_explanation_editor_keeps_exam_system_and_reasoning(self):
        self.responses=[httpx.Response(200,json=completion())]
        result,_=llm.generate('解析编辑',{'_jlpt_role':'explanation_editor','count':2},{'ok':True})
        payload=json.loads(self.requests[0].content)
        self.assertEqual(payload['thinking'],{'type':'enabled'})
        self.assertIn('解析和审查反馈用中文',payload['messages'][0]['content'])
        self.assertEqual(result['_generation_meta']['role'],'explanation_editor')

    def test_full_paper_has_room_for_reasoning_without_disabling_it(self):
        self.responses=[httpx.Response(200,json=completion())]
        result,_=llm.generate('整卷审查',{'_jlpt_role':'global_reviewer','count':91},{'ok':True})
        payload=json.loads(self.requests[0].content)
        self.assertEqual(payload['thinking'],{'type':'enabled'})
        self.assertEqual(payload['reasoning_effort'],'low')
        self.assertEqual(payload['max_tokens'],32768)
        self.assertEqual(result['_generation_meta']['max_tokens'],32768)

    def test_sdk_retries_once_for_transient_status(self):
        for status in (429, 500, 503):
            with self.subTest(status=status):
                self.requests.clear()
                self.responses = [httpx.Response(status, json={'error': 'private'}),
                                  httpx.Response(200, json=completion())]
                self.generate()
                self.assertEqual(len(self.requests), 2)

    def test_retry_exhaustion_does_not_expose_provider_error(self):
        self.responses = [httpx.Response(429, text='test-secret private error') for _ in range(2)]
        with self.assertRaises(llm.AppError) as error:
            self.generate()
        self.assertEqual(len(self.requests), 2)
        self.assertIn('额度不足', str(error.exception))
        self.assertNotIn('test-secret', str(error.exception))

    def test_auth_and_bad_requests_are_not_retried(self):
        for status in (400, 401, 403):
            with self.subTest(status=status):
                self.requests.clear()
                self.responses = [httpx.Response(status, text='private provider error')]
                with self.assertRaises(llm.AppError) as error:
                    self.generate()
                self.assertEqual(len(self.requests), 1)
                self.assertNotIn('private', str(error.exception))

    def test_redirect_never_sends_credentials_to_second_host(self):
        self.responses = [httpx.Response(302, headers={'location': 'https://other.test/'})]
        with self.assertRaisesRegex(llm.AppError, '重定向'):
            self.generate()
        self.assertEqual(len(self.requests), 1)

    def test_connection_failures_use_sdk_bounded_retry(self):
        self.responses = [httpx.ConnectError('private network details') for _ in range(2)]
        with self.assertRaisesRegex(llm.AppError, '网络连接失败'):
            self.generate()
        self.assertEqual(len(self.requests), 2)

    def test_stream_read_error_is_safe_and_response_closed(self):
        class FailingStream(httpx.SyncByteStream):
            closed = False
            def __iter__(self):
                yield b'{'
                raise httpx.ReadTimeout('private connection details')
            def close(self):
                self.closed = True
        stream = FailingStream()
        self.responses = [httpx.Response(200, stream=stream)]
        with self.assertRaisesRegex(llm.AppError, '超时'):
            self.generate()
        self.assertTrue(stream.closed)
        self.assertEqual(len(self.requests), 1)

    def test_oversized_stream_stops_reading_and_closes(self):
        class LargeStream(httpx.SyncByteStream):
            chunks = 0
            closed = False
            def __iter__(self):
                for _ in range(100):
                    self.chunks += 1
                    yield b' ' * 65_536
            def close(self):
                self.closed = True
        stream = LargeStream()
        self.responses = [httpx.Response(200, stream=stream)]
        with self.assertRaisesRegex(llm.AppError, '返回过大'):
            self.generate()
        self.assertLess(stream.chunks, 100)
        self.assertTrue(stream.closed)

    def test_truncated_output_is_rejected(self):
        self.responses = [httpx.Response(200, json=completion(finish_reason='length'))]
        with self.assertRaisesRegex(llm.AppError, '截断'):
            self.generate()

    def test_fenced_json_and_missing_model_remain_compatible(self):
        body = completion('```json\n{"ok": true}\n```')
        del body['model']
        self.responses = [httpx.Response(200, json=body)]
        self.assertEqual(self.generate(), ({'ok': True}, 'configured-model'))

    def test_malformed_outputs_are_not_accepted(self):
        for body in ({}, {'choices': []}, completion(None), completion('[]'), completion('bad')):
            with self.subTest(body=body):
                self.responses = [httpx.Response(200, json=body)]
                with self.assertRaisesRegex(llm.AppError, '未返回可解析'):
                    self.generate()

    def test_invalid_urls_and_missing_credentials_never_send(self):
        for base in ('http://example.test', 'https://user:pass@example.test',
                     'https://example.test?secret=x', 'https://[broken',
                     'https://example.test:bad'):
            self.config['base'] = base
            with self.assertRaisesRegex(llm.AppError, 'HTTPS'):
                self.generate()
        self.config['key'] = ''
        with self.assertRaisesRegex(llm.AppError, 'LLM_API_KEY'):
            self.generate()
        self.assertEqual(self.requests, [])

    def sse(self, fragments, finish='stop'):
        chunks = []
        for fragment in fragments:
            chunks.append({'id':'sse-test','object':'chat.completion.chunk','created':1,'model':'actual-stream-model',
                           'choices':[{'index':0,'delta':{'content':fragment},'finish_reason':None}]})
        chunks.append({'id':'sse-test','object':'chat.completion.chunk','created':1,'model':'actual-stream-model',
                       'choices':[{'index':0,'delta':{},'finish_reason':finish}]})
        body = ''.join('data: '+json.dumps(c,ensure_ascii=False)+'\n\n' for c in chunks)+'data: [DONE]\n\n'
        return httpx.Response(200,content=body.encode(),headers={'Content-Type':'text/event-stream'})

    def test_real_sdk_stream_emits_readable_japanese_then_validates_json(self):
        self.responses=[self.sse(['{"jp":"水', 'をください。",', '"ok":true}'])]
        events=[]
        d,model=llm.generate_stream('test',{}, {'jp':'日语'},events.append,lambda:False)
        self.assertEqual(d,{'jp':'水をください。','ok':True})
        self.assertEqual(model,'actual-stream-model')
        self.assertEqual(events[0]['text'],'水')
        self.assertEqual(events[-1]['text'],'水をください。')
        payload=json.loads(self.requests[0].content)
        self.assertTrue(payload['stream'])
        self.assertEqual(payload['thinking'],{'type':'enabled'})
        self.assertEqual(payload['reasoning_effort'],'high')

    def test_stream_truncation_and_cancel_do_not_return_partial_objects(self):
        self.responses=[self.sse(['{"jp":"水"}'],finish='length')]
        with self.assertRaisesRegex(llm.AppError,'截断'):
            llm.generate_stream('test',{}, {},lambda e:None,lambda:False)
        self.responses=[self.sse(['{"jp":"水','です。"}'])]
        stopped=[False]
        def emit(event): stopped[0]=True
        with self.assertRaisesRegex(llm.AppError,'停止'):
            llm.generate_stream('test',{}, {},emit,lambda:stopped[0])

    def test_stream_reasoning_is_not_exposed_as_chat_text(self):
        hidden={'id':'reasoning','object':'chat.completion.chunk','created':1,'model':'deepseek-flash',
                'choices':[{'index':0,'delta':{'reasoning_content':'private reasoning'},'finish_reason':None}]}
        content=('data: '+json.dumps(hidden)+'\n\n').encode()+self.sse(['{"jp":"こんにちは"}']).content
        self.responses=[httpx.Response(200,content=content,headers={'Content-Type':'text/event-stream'})]
        events=[]
        result,_=llm.generate_stream('test',{}, {'jp':'日语'},events.append,lambda:False)
        self.assertEqual(result,{'jp':'こんにちは'})
        self.assertEqual(events,[{'type':'delta','text':'こんにちは'}])


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.enterContext(patch('llm.ROOT', self.root))
        self.enterContext(patch.dict(os.environ, {}, clear=True))

    def test_dotenv_quotes_comments_bom_and_literal_secrets(self):
        (self.root / '.env').write_text(
            '\ufeffexport LLM_MODEL_ID="model-name" # comment\n'
            "LLM_API_KEY='literal#${TOKEN}'\nLLM_TIMEOUT=500\n", encoding='utf-8')
        result = llm.configuration()
        self.assertEqual(result['model'], 'model-name')
        self.assertEqual(result['key'], 'literal#${TOKEN}')
        self.assertEqual(result['timeout'], 90)
        self.assertNotIn('LLM_API_KEY', os.environ)
        self.assertEqual(set(llm.public_config()), {'configured', 'model', 'provider'})

    def test_environment_aliases_override_file_and_file_is_reloaded(self):
        path = self.root / '.env'
        path.write_text('LLM_MODEL_ID=file-model\nLLM_API_KEY=file-key\n')
        with patch.dict(os.environ, {'OPENAI_MODEL': 'env-model', 'OPENAI_API_KEY': 'env-key'}):
            self.assertEqual(llm.configuration()['model'], 'env-model')
            self.assertEqual(llm.configuration()['key'], 'env-key')
        path.write_text('LLM_MODEL_ID=updated-model\n')
        self.assertEqual(llm.configuration()['model'], 'updated-model')

    def test_explicit_empty_env_does_not_restore_same_file_key(self):
        (self.root / '.env').write_text('LLM_API_KEY=file-key\n')
        with patch.dict(os.environ, {'LLM_API_KEY': ''}):
            self.assertEqual(llm.configuration()['key'], '')

    def test_invalid_url_does_not_break_offline_config_display(self):
        (self.root / '.env').write_text('LLM_BASE_URL=https://[broken\nLLM_TIMEOUT=invalid\n')
        self.assertEqual(llm.public_config()['provider'], '地址无效')
        self.assertEqual(llm.configuration()['timeout'], 60)


if __name__ == '__main__':
    unittest.main()
