"""AI settings persistence uses temporary .env files, never project credentials."""
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import llm
from service import Service


class AIConfigTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / '.env'
        self.enterContext(patch('llm.ROOT', self.root))
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.values = dict(base='https://example.com/v1', model='test-model', key='fixture-key', timeout=30)

    def test_create_reload_permissions_and_no_secret_response(self):
        result = llm.save_config(self.values)
        self.assertEqual(llm.configuration(), dict(base=self.values['base'], model='test-model', key='fixture-key', timeout=30))
        if os.name != 'nt':  # Windows uses inherited ACLs, not POSIX mode bits.
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(result['key_configured'])
        self.assertNotIn('fixture-key', json.dumps(result))
        self.assertNotIn('key', result)
        self.assertEqual(llm.editable_config(), result)

    def test_blank_secret_preserves_alias_comments_and_unrelated_settings(self):
        original = "# keep me\nOPENAI_API_KEY='old-key'\nUNRELATED='do not change'\n"
        self.path.write_text(original)
        llm.save_config(dict(self.values, key=''))
        self.assertEqual(llm.configuration()['key'], 'old-key')
        self.assertIn(original, self.path.read_text())
        self.assertNotIn('LLM_API_KEY', self.path.read_text())

    def test_literals_bom_duplicates_and_multiple_updates(self):
        self.path.write_text('\ufeffLLM_MODEL_ID=first\nLLM_MODEL_ID=second\n# retain\n')
        key = r"literal#${TOKEN}'quote\path\\end" + chr(92)
        llm.save_config(dict(self.values, key=key))
        self.assertEqual(llm.configuration()['key'], key)
        llm.save_config(dict(self.values, model='updated', key=''))
        self.assertEqual(llm.configuration()['model'], 'updated')
        self.assertEqual(llm.configuration()['key'], key)
        self.assertIn('# retain', self.path.read_text())

    def test_invalid_input_does_not_change_existing_file(self):
        original = b'# unchanged\nLLM_API_KEY=old\n'
        self.path.write_bytes(original)
        cases = [dict(base=b) for b in ['http://example.com', 'https://user:secret@example.com',
                 'https://example.com?key=secret', 'https://example.com/#x', 'https://[bad',
                 'https://example.com:bad', 'https://exa mple.com']]
        cases += [dict(timeout=t) for t in [True, float('nan'), float('inf'), 9, 91, '30']]
        cases += [dict(model=''), dict(key='secret\nINJECTED=yes'), dict(base='https://example.com\n'), dict(key=None)]
        for changes in cases:
            with self.subTest(changes=list(changes)):
                with self.assertRaises(llm.AppError): llm.save_config(dict(self.values, **changes))
                self.assertEqual(self.path.read_bytes(), original)

    def test_missing_key_requires_input(self):
        with self.assertRaisesRegex(llm.AppError, 'API 密钥'):
            llm.save_config(dict(self.values, key=''))
        self.assertFalse(self.path.exists())

    def test_failed_atomic_replace_leaves_original_and_removes_temp(self):
        self.path.write_text('LLM_API_KEY=old-key\n')
        original = self.path.read_bytes()
        with patch.object(Path, 'replace', side_effect=OSError('sensitive diagnostic')):
            with self.assertRaisesRegex(llm.AppError, '无法保存项目 .env') as error:
                llm.save_config(self.values)
        self.assertNotIn('sensitive diagnostic', str(error.exception))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['.env', '.env.lock'])

    def test_fsync_handle_is_writable_and_closed_before_replace(self):
        fsync, replace = os.fsync, Path.replace
        handles = []
        def require_writable(fd):
            # A zero-byte write catches a read-only descriptor on every OS.
            os.write(fd, b'')
            fsync(fd)
            handles.append(fd)
        def require_closed(source, destination):
            self.assertTrue(handles)
            with self.assertRaises(OSError): os.fstat(handles[-1])
            return replace(source, destination)
        with patch('llm.os.fsync', side_effect=require_writable), patch.object(
                Path, 'replace', new=require_closed):
            llm.save_config(self.values)
        self.assertEqual(llm.configuration()['key'], 'fixture-key')

    def test_failed_fsync_preserves_original_and_removes_temp(self):
        self.path.write_text('LLM_API_KEY=old-key\n')
        original = self.path.read_bytes()
        with patch('llm.os.fsync', side_effect=OSError('sensitive diagnostic')):
            with self.assertRaisesRegex(llm.AppError, '无法保存项目 .env') as error:
                llm.save_config(self.values)
        self.assertNotIn('sensitive diagnostic', str(error.exception))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['.env', '.env.lock'])

    def test_environment_priority_is_visible_and_not_persisted(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'env-secret', 'OPENAI_MODEL': 'env-model'}):
            result = llm.save_config(dict(self.values, key=''))
            self.assertEqual(result['model'], 'env-model')
            self.assertEqual(result['overrides'], ['OPENAI_API_KEY', 'OPENAI_MODEL'])
            self.assertNotIn('env-secret', self.path.read_text())
            self.assertNotIn('env-secret', json.dumps(result))
        self.assertEqual(llm.configuration()['model'], 'test-model')
        self.assertFalse(llm.editable_config()['key_configured'])

    def test_rpc_and_export_do_not_return_credentials(self):
        app = Service(self.root / 'data')
        self.addCleanup(app.close)
        app.route('config_save', self.values)
        for action in ['config_get', 'bootstrap']:
            self.assertNotIn('fixture-key', json.dumps(app.route(action, {})))
        export = Path(app.route('export', {'type': 'json'})['path']).read_text()
        self.assertNotIn('fixture-key', export)
        self.assertNotIn('LLM_API_KEY', export)

    def test_config_write_waits_for_other_process_lock(self):
        llm.save_config(self.values)
        before = self.path.read_bytes()
        code = ('import json,sys; from pathlib import Path; '
                'sys.path.insert(0,sys.argv[1]); import llm; '
                'llm.ROOT=Path(sys.argv[2]); print("ready",flush=True); '
                'llm.save_config(json.loads(sys.argv[3]))')
        with llm.portalocker.Lock(self.root / '.env.lock', mode='a', timeout=2):
            child = subprocess.Popen([sys.executable, '-c', code,
                str(Path(__file__).resolve().parents[1] / 'backend'), str(self.root),
                json.dumps(dict(self.values, model='updated-under-lock'))], stdout=subprocess.PIPE)
            try:
                self.assertEqual(child.stdout.readline().strip(), b'ready')
                with self.assertRaises(subprocess.TimeoutExpired): child.wait(timeout=.2)
                self.assertEqual(self.path.read_bytes(), before)
            except BaseException:
                child.kill()
                child.wait()
                child.stdout.close()
                raise
        try:
            self.assertEqual(child.wait(timeout=5), 0)
            self.assertEqual(llm.configuration()['model'], 'updated-under-lock')
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            child.stdout.close()


if __name__ == '__main__': unittest.main()
