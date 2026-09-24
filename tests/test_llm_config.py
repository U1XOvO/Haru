"""Offline provider persistence and outgoing request contract tests."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import llm
import llm_config


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Keep Windows system variables needed by OpenSSL while isolating legacy AI keys.
        env = patch.dict(os.environ, {'LLM_API_KEY': '', 'LLM_MODEL_ID': ''})
        env.start(); self.addCleanup(env.stop)
        root = patch.object(llm_config, 'config_root', return_value=self.root)
        root.start(); self.addCleanup(root.stop)

    def row(self, identity='second', **values):
        return dict(llm_config.profile_defaults(), id=identity, name=identity,
                    base='https://example.com/v1', key='fixture-new', model='new-model', timeout=30) | values

    def save(self, rows, active, revision=0):
        return llm_config.save_config(dict(providers=rows, active=active, revision=revision))

    def test_only_saved_profiles_are_used_and_secrets_stay_private(self):
        (self.root / '.env').write_text('LLM_API_KEY=fixture-old\nLLM_MODEL_ID=old-model\n')
        with patch.dict(os.environ, {'LLM_API_KEY':'environment', 'LLM_MODEL_ID':'environment'}):
            initial = llm_config.editable_config()
            self.assertEqual(initial['active'], 'default')
            self.assertEqual(initial['model'], '')
            self.assertFalse(initial['key_configured'])
            self.assertEqual(llm_config.configuration()['key'], '')
        saved = self.save([self.row('first', key='fixture-first', model='first-model'), self.row()], 'second')
        self.assertEqual(llm_config.configuration()['key'], 'fixture-new')
        self.assertNotIn('fixture-new', json.dumps(saved))
        if os.name != 'nt':
            self.assertEqual((self.root / '.llm-providers.json').stat().st_mode & 0o777, 0o600)
        saved = self.save(saved['providers'], 'first', saved['revision'])
        with patch.dict(os.environ, {'LLM_API_KEY':'different', 'LLM_MODEL_ID':'environment'}):
            self.assertEqual(llm_config.configuration()['model'], 'first-model')
            self.assertEqual(llm_config.configuration()['key'], 'fixture-first')
        self.assertIn('fixture-old', (self.root / '.env').read_text())
        self.save([saved['providers'][1]], 'second', saved['revision'])
        self.assertEqual(len(llm_config.editable_config()['providers']), 1)

    def test_key_cannot_leak_to_new_destination(self):
        saved=self.save([self.row()],'second')
        old=saved['providers'][0]
        old['base']='https://other.example/v1'
        with self.assertRaisesRegex(llm.AppError,'地址已改变'):
            self.save([old],'second',saved['revision'])
        self.assertEqual(llm_config.configuration()['base'],'https://example.com/v1')
        old['key']='explicit-new'
        self.save([old],'second',saved['revision'])
        self.assertEqual(llm_config.configuration()['key'],'explicit-new')

    def test_stale_save_and_atomic_failure_preserve_file(self):
        saved=self.save([self.row()],'second')
        before=(self.root / '.llm-providers.json').read_bytes()
        with self.assertRaisesRegex(llm.AppError,'配置已更新'):
            self.save([self.row()],'second')
        with patch.object(Path,'replace',side_effect=OSError()), self.assertRaises(llm.AppError):
            self.save([self.row(model='changed')],'second',saved['revision'])
        self.assertEqual(before,(self.root / '.llm-providers.json').read_bytes())

    def test_corrupt_saved_profiles_are_rejected(self):
        row=self.row()
        for data in [
                {'version':1,'revision':1,'active':'second','providers':[row,row]},
                {'version':1,'revision':True,'active':'second','providers':[row]},
                {'version':1,'revision':1,'active':'second','providers':[row | {'key':''}]},
        ]:
            with self.subTest(data=data):
                (self.root / '.llm-providers.json').write_text(json.dumps(data))
                with self.assertRaisesRegex(llm.AppError,'无法读取'):
                    llm_config.read_profiles()

    def test_provider_config_symlink_is_rejected(self):
        config=self.root / '.llm-providers.json'
        target=self.root / 'external.json'
        target.write_text('{}')
        config.symlink_to(target)
        with self.assertRaisesRegex(llm.AppError,'无法读取'):
            llm_config.read_profiles()
        config.unlink()
        config.symlink_to(self.root / 'missing.json')
        with self.assertRaisesRegex(llm.AppError,'无法读取'):
            llm_config.read_profiles()

    def test_invalid_parameters_never_persist(self):
        for values in [dict(temperature=float('nan')),dict(retries=True),dict(max_tokens=1.5),
                       dict(timeout=100),dict(base='http://example.com'),
                       dict(reasoning='custom',reasoning_custom=''),
                       dict(reasoning='custom',reasoning_custom='high value')]:
            with self.subTest(values=values), self.assertRaises(llm.AppError):
                self.save([self.row(**values)],'second')
        for rows,active in [([], 'second'),([self.row(),self.row()],'second'),([self.row()],'missing')]:
            with self.assertRaises(llm.AppError): self.save(rows,active)
        self.assertFalse((self.root / '.llm-providers.json').exists())

    def test_task_budgets_use_openai_compatible_max_tokens(self):
        cases=[({},5000),(None,5000),({'lesson_design':True},10000),
               ({'_jlpt_role':'author','count':2},8000),
               ({'_jlpt_role':'reviewer','count':2,'type_id':'grammar_order'},8000),
               ({'_jlpt_role':'explanation_editor','count':4},8000),
               ({'_jlpt_role':'global_reviewer','count':61},12000)]
        for context,tokens in cases:
            with self.subTest(context=context):
                payload=llm.build_payload(self.row(),'task',context,{})
                self.assertEqual(payload['max_tokens'],tokens)
                self.assertNotIn('max_completion_tokens',payload)
                self.assertNotIn('reasoning_effort',payload)

    def test_explicit_parameters_override_every_task(self):
        c=self.row(reasoning='off',max_tokens=900,temperature=0.2,top_p=0.8)
        for context in [{},{'lesson_design':True},{'_jlpt_role':'global_reviewer','count':80}]:
            payload=llm.build_payload(c,'task',context,{})
            self.assertEqual(payload['reasoning_effort'],'none')
            self.assertEqual(payload['max_tokens'],900)
            self.assertNotIn('max_completion_tokens',payload)
            self.assertEqual(payload['temperature'],0.2)
            self.assertEqual(payload['top_p'],0.8)
        payload=llm.build_payload(c | dict(reasoning='omit',omit_temperature=True),'task',{}, {})
        self.assertNotIn('extra_body',payload)
        self.assertNotIn('temperature',payload)
        payload=llm.build_payload(self.row(reasoning='high'),'task',{}, {})
        self.assertEqual(payload['reasoning_effort'],'high')
        self.assertNotIn('extra_body',payload)
        payload=llm.build_payload(self.row(reasoning='custom',reasoning_custom='xhigh'),'task',{}, {})
        self.assertEqual(payload['reasoning_effort'],'xhigh')
        payload=llm.build_payload(c | dict(omit_token_limit=True),'task',{}, {})
        self.assertNotIn('max_tokens',payload)
        self.assertNotIn('max_completion_tokens',payload)

    def test_removed_legacy_parameters_are_ignored_and_not_saved(self):
        old=self.row(adapter='deepseek',token_parameter='max_completion_tokens',reasoning='auto',
                     frequency_penalty=1,presence_penalty=1,seed=7)
        legacy_payload=llm.build_payload(old,'task',{}, {})
        self.assertNotIn('reasoning_effort',legacy_payload)
        self.save([old],'second')
        saved=llm_config.configuration()
        for key in ('adapter','token_parameter','frequency_penalty','presence_penalty','seed'):
            self.assertNotIn(key,saved)
        self.assertEqual(saved['reasoning'],'omit')
        payload=llm.build_payload(saved,'task',{}, {})
        self.assertIn('max_tokens',payload)
        self.assertNotIn('max_completion_tokens',payload)

    def test_deepseek_explicit_timeout_reaches_sdk(self):
        import httpx
        import openai
        self.save([self.row(base='https://api.deepseek.com/v1',timeout=30,
                            max_tokens=321,temperature=0.4,retries=0,reasoning='omit')],'second')
        self.assertEqual(llm_config.configuration()['timeout'],30)
        requests=[]
        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200,json={'model':'returned','choices':[{'finish_reason':'stop','message':{'content':'{"ok":true}'}}]})
        real=openai.OpenAI
        settings=[]
        def client(**kw):
            settings.append(kw.copy())
            kw['http_client'].close()
            kw['http_client']=httpx.Client(transport=httpx.MockTransport(respond))
            return real(**kw)
        with patch.object(openai,'OpenAI',side_effect=client):
            self.assertEqual(llm.generate('test',{}, {'ok':True}),({'ok':True},'returned'))
        self.assertEqual(requests[0]['model'],'new-model')
        self.assertEqual(requests[0]['temperature'],0.4)
        self.assertEqual(requests[0]['max_tokens'],321)
        self.assertEqual(settings[0]['base_url'],'https://api.deepseek.com/v1')
        self.assertEqual(settings[0]['max_retries'],0)
        self.assertEqual(settings[0]['timeout'],30)


if __name__ == '__main__': unittest.main()
