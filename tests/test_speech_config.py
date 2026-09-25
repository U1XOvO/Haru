"""Speech settings use independent, secret-safe local storage."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from llm import AppError
import speech_config


class SpeechConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_three_distinct_long_japanese_samples(self):
        defaults = speech_config.defaults()
        voices = defaults['voices']
        self.assertEqual([v['id'] for v in voices], ['girl', 'student', 'boy'])
        self.assertEqual(len({v['theme'] for v in voices}), 3)
        self.assertTrue(all(len(v['sample']) > 200 for v in voices))
        self.assertEqual((defaults['timeout'], defaults['retries']), (90, 0))

    def test_key_is_not_returned_and_blank_input_keeps_old_key(self):
        first = speech_config.defaults() | {'key': 'fixture-secret', 'engine': 'gemini'}
        visible = speech_config.save_settings(first, self.root)
        self.assertTrue(visible['key_configured'])
        self.assertNotIn('key', visible)
        self.assertEqual(speech_config.read_settings(self.root)['key'], 'fixture-secret')
        visible['key'] = ''
        again = speech_config.save_settings(visible, self.root)
        self.assertEqual(speech_config.read_settings(self.root)['key'], 'fixture-secret')
        self.assertEqual(again['revision'], 2)
        if sys.platform != 'win32':
            self.assertEqual(speech_config.config_path(self.root).stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(AppError, '已更新'):
            speech_config.save_settings(first, self.root)
        again['clear_key'] = True
        speech_config.save_settings(again, self.root)
        self.assertEqual(speech_config.read_settings(self.root)['key'], '')

    def test_custom_voice_and_persona_change_require_new_voice(self):
        config = speech_config.defaults()
        config['voices'].append({
            'id': 'custom_test', 'name': '暖声', 'gender': 'neutral',
            'description': 'Warm, clear Japanese voice', 'theme': '晚餐',
            'sample': '今日は夕食を作ります。'
        })
        config['selected'] = 'custom_test'
        speech_config.save_settings(config, self.root)
        stored = speech_config.read_settings(self.root)
        stored['voices'][-1]['voice_id'] = 'voice_fixture123'
        speech_config._atomic_save(self.root, stored)
        visible = speech_config.editable_settings(self.root)
        visible['voices'][-1]['description'] = 'A softer Japanese voice'
        speech_config.save_settings(visible, self.root)
        self.assertEqual(speech_config.read_settings(self.root)['voices'][-1]['voice_id'], '')

    def test_invalid_storage_is_preserved(self):
        path = speech_config.config_path(self.root)
        path.write_text('{broken', encoding='utf-8')
        with self.assertRaisesRegex(AppError, '原文件已保留'):
            speech_config.read_settings(self.root)
        self.assertEqual(path.read_text(encoding='utf-8'), '{broken')
        path.unlink()
        outside = self.root / 'outside'
        outside.write_text('{}')
        path.symlink_to(outside)
        with self.assertRaisesRegex(AppError, '文件无效'):
            speech_config.read_settings(self.root)
        self.assertEqual(outside.read_text(), '{}')

    def test_ipc_settings_are_independent_of_learning_database(self):
        env=dict(os.environ, HARU_STORAGE_DIR=str(self.root),
                 HARU_DATA_DIR=str(self.root / 'runtime'), PYTHONDONTWRITEBYTECODE='1')
        def route(action, params=None):
            result=subprocess.run(
                [sys.executable, str(ROOT / 'backend/bridge.py')],
                input=json.dumps({'action':action,'params':params or {}}),
                text=True,encoding='utf-8',capture_output=True,env=env,timeout=5,check=True)
            self.assertFalse(result.stderr)
            return json.loads(result.stdout)
        current=route('speech_settings_get')
        self.assertTrue(current['ok'])
        self.assertEqual(current['data']['engine'],'edge')
        self.assertNotIn('key',current['data'])
        params=current['data'] | {'key':'fixture-secret','engine':'gemini'}
        saved=route('speech_settings_save',params)
        self.assertTrue(saved['ok'],saved)
        self.assertTrue(saved['data']['key_configured'])
        self.assertNotIn('fixture-secret',json.dumps(saved))
        self.assertFalse((self.root / 'runtime/haru.sqlite3').exists())
        self.assertEqual(route('speech_settings_get')['data']['selected'],'girl')


if __name__ == '__main__':
    unittest.main()
