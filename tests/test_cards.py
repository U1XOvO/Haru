"""Random batches and saved-card history, using an isolated database and mock AI."""
import copy
import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from curriculum import CARDS
from llm import AppError
from service import Service


def card(word):
    return dict(copy.deepcopy(CARDS[0]),word=word)


class CardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.app=Service(self.temp.name)
    def tearDown(self):
        self.app.close();self.temp.cleanup()
    def test_requested_batch_sizes_and_persistence(self):
        for count in (1,5,10):
            items=[card(f'テスト{count}-{i}') for i in range(count)]
            with patch('service.generate',return_value=({'cards':items},'fixture')) as ai:
                result=self.app.route('card_random',{'count':count})
            self.assertEqual(len(result['generated']),count)
            self.assertEqual(ai.call_args.args[1]['count'],count)
            self.assertTrue(all(c['created'] and c['model']=='fixture' for c in result['generated']))
        other=Service(self.temp.name)
        try:self.assertEqual(len(other.cards({})),16)
        finally:other.close()
    def test_history_and_within_batch_duplicates_retry_only_missing(self):
        self.app.add_card(card('コーヒー'))
        replies=[({'cards':[card('ｺｰﾋｰ'),card('猫'),card('猫')]},'fixture'),
                 ({'cards':[card('水'),card('駅')]},'fixture')]
        with patch('service.generate',side_effect=replies) as ai:
            result=self.app.card_random({'count':3})
        self.assertEqual([c['word'] for c in result['generated']],['猫','水','駅'])
        self.assertEqual(ai.call_args_list[1].args[1]['count'],2)
        self.assertIn('猫',ai.call_args_list[1].args[1]['exclude_words'])
        self.assertEqual(len(result['cards']),4)
    def test_repeated_duplicates_leave_no_partial_batch(self):
        self.app.add_card(card('猫'))
        with patch('service.generate',side_effect=[({'cards':[card('猫'),card('水')]},'fixture'),({'cards':[card('猫')]},'fixture'),({'cards':[card('水')]},'fixture')]) as ai:
            with self.assertRaisesRegex(AppError,'本批未保存'):self.app.card_random({'count':2})
        self.assertEqual(ai.call_count,3)
        self.assertEqual([c['word'] for c in self.app.cards({})],['猫'])
    def test_invalid_counts_do_not_call_ai(self):
        with patch('service.generate') as ai:
            for count in (0,11,-1,True,'5',5.5,None):
                with self.assertRaises(AppError):self.app.card_random({'count':count})
            ai.assert_not_called()
    def test_invalid_or_failed_response_leaves_database_unchanged(self):
        for response in ({'cards':[]},{'cards':[card('猫'),{}]},{'cards':[card('猫')]*3}):
            with patch('service.generate',return_value=(response,'fixture')):
                with self.assertRaises(AppError):self.app.card_random({'count':2})
            self.assertEqual(self.app.cards({}),[])
        with patch('service.generate',side_effect=[({'cards':[card('猫')]},'fixture'),AppError('连接失败')]):
            with self.assertRaises(AppError):self.app.card_random({'count':2})
        self.assertEqual(self.app.cards({}),[])
    def test_recent_history_includes_reviewed_cards_without_changing_schedule(self):
        self.app.add_card(card('猫'));self.app.add_card(card('水'))
        recent=self.app.cards({'order':'recent'})
        self.assertEqual([c['word'] for c in recent],['水','猫'])
        first=recent[0];self.app.review({'id':first['id'],'quality':4})
        before=[tuple(r) for r in self.app.db.execute('SELECT * FROM cards')]
        history=self.app.route('cards',{'order':'recent'})
        self.assertFalse(history[0]['ready'])
        self.assertEqual(before,[tuple(r) for r in self.app.db.execute('SELECT * FROM cards')])
        self.assertEqual(self.app.stats()['today'],1)
    def test_legacy_card_has_no_fabricated_created_time_and_is_deduplicated(self):
        old=card(' コーヒー ')
        with self.app.db:self.app.db.execute('INSERT INTO cards(id,word,data,due) VALUES (?,?,?,?)',('old',old['word'],json.dumps(old),'2000-01-01'))
        self.app.add_card(card('ｺｰﾋｰ'))
        self.assertEqual(len(self.app.cards({})),1)
        self.assertNotIn('created',self.app.cards({})[0])
    def test_same_reading_different_words_remain_distinct(self):
        self.app.add_card(dict(card('橋'),reading='はし'))
        self.app.add_card(dict(card('箸'),reading='はし'))
        self.assertEqual(len(self.app.cards({})),2)
    def test_concurrent_batches_are_atomic_and_unique(self):
        def save():
            app=Service(self.temp.name)
            try:return app.store_cards([card('水'),card('猫')],require_new=True)
            finally:app.close()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:save(),range(2)))
        self.assertEqual(sorted(map(len,results)),[0,2])
        self.assertEqual(len(self.app.cards({})),2)

if __name__=='__main__':unittest.main()
