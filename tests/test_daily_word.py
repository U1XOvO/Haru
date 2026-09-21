"""Opening-session teaching content, isolated storage and mocked model responses."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from curriculum import CARDS
from llm import AppError
from service import Service


class DailyWordTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app=Service(self.temp.name)
        self.addCleanup(self.app.close)

    def test_each_open_calls_ai_including_same_day_and_after_restart(self):
        with patch('service.generate',side_effect=lambda *args:(copy.deepcopy(CARDS[0]),'actual-model')) as generate:
            self.app.bootstrap({})
            generate.assert_not_called()
            first=self.app.route('daily_word',{})
            other=Service(self.temp.name)
            try: second=other.route('daily_word',{})
            finally: other.close()
            self.assertEqual(generate.call_count,2)
            self.assertNotEqual(first['id'],second['id'])
            self.assertEqual(second['model'],'actual-model')
            self.assertIn(first['word'],generate.call_args.args[1]['recent_words'])
        self.assertEqual(self.app.cards({}),[])
        self.assertEqual(self.app.stats()['today'],0)

    def test_save_exact_displayed_word_without_another_ai_call(self):
        with patch('service.generate',return_value=(copy.deepcopy(CARDS[0]),'actual-model')) as generate:
            daily=self.app.route('daily_word',{})
            for _ in range(2): cards=self.app.route('daily_word_add',{'id':daily['id']})
            self.assertEqual(generate.call_count,1)
        self.assertEqual(len(cards),1)
        for key in (*CARDS[0],'source','model'):
            self.assertEqual(cards[0][key],daily[key])
        self.assertNotEqual(cards[0]['id'],daily['id'])
        lesson=self.app.lesson({'day':1})
        with self.assertRaises(AppError): self.app.daily_word_add({'id':lesson['id']})

    def test_invalid_or_failed_generation_does_not_save_placeholder(self):
        for result in ({},dict(CARDS[0],reading=''),dict(CARDS[0],word='あ'*31)):
            with patch('service.generate',return_value=(result,'fixture')):
                with self.assertRaises(AppError): self.app.daily_word({})
        with patch('service.generate',side_effect=AppError('连接失败')):
            with self.assertRaisesRegex(AppError,'连接失败'): self.app.daily_word({})
        self.assertEqual(self.app.history('daily_word'),[])
        self.assertEqual(self.app.cards({}),[])


if __name__=='__main__': unittest.main()
