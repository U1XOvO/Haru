"""Bounded card APIs and unchanged review scheduling, using disposable data only."""
import json
from datetime import datetime, timedelta
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from llm import AppError
from service import Service


class CardPerformanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.app = Service(Path(temporary.name))
        self.addCleanup(self.app.close)

    def card(self, word, **extra):
        return dict(word=word, reading='たんご', romaji='tango', meaning='中文词义',
                    example='単語を読みます。', translation='读单词。', mnemonic='放进句子。', **extra)

    def add(self, count):
        return self.app.store_cards([self.card(f'単語{i:04}') for i in range(count)])

    def test_keyset_pages_are_bounded_and_stable_when_new_card_is_added(self):
        original = self.add(123)
        page = self.app.cards_page({'limit': 30})
        self.assertEqual(len(page['items']), 30)
        self.assertEqual(page['counts']['total'], 123)
        self.assertNotIn('example', page['items'][0])
        seen = [item['id'] for item in page['items']]
        self.app.store_cards([self.card('新しい単語')])
        while page['next_cursor'] is not None:
            page = self.app.cards_page({'limit': 30, 'before': page['next_cursor']})
            self.assertLessEqual(len(page['items']), 30)
            seen.extend(item['id'] for item in page['items'])
        self.assertEqual(seen, list(reversed(original)))
        self.assertEqual(len(self.app.cards({})), 124)  # Legacy export remains complete.

    def test_search_normalizes_width_case_and_treats_wildcards_literally(self):
        self.add(60)
        self.app.store_cards([self.card('Ｃａｆé', source='内置原创'),
                              self.card('100%'), self.card('under_score')])
        self.assertEqual(self.app.cards_page({'query': 'café'})['items'][0]['word'], 'Ｃａｆé')
        self.assertEqual(len(self.app.cards_page({'query': '%'})['items']), 1)
        self.assertEqual(len(self.app.cards_page({'query': '_'})['items']), 1)
        self.assertEqual(len(self.app.cards_page({'query': 'TANGO', 'limit': 7})['items']), 7)
        self.assertEqual(len(self.app.cards_page({'query': '中文', 'limit': 8})['items']), 8)
        self.assertEqual(self.app.cards_page({'query': 'not present'})['items'], [])

    def test_due_queue_boundary_and_review_are_incremental_and_idempotent(self):
        ids = self.add(12)
        fixed = datetime(2026, 9, 22, 12, 0)
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return fixed
        with self.app.db:
            self.app.db.execute('UPDATE cards SET due=?', (fixed.isoformat(timespec='seconds'),))
            self.app.db.execute('UPDATE cards SET due=? WHERE id=?',
                                ((fixed+timedelta(seconds=1)).isoformat(timespec='seconds'), ids[-1]))
        with patch('service.datetime', Clock):
            queue = self.app.card_queue({})
            self.assertEqual(len(queue['items']), 5)
            self.assertEqual(queue['counts'], {'total': 12, 'due': 11})
            self.assertNotIn(ids[-1], [item['id'] for item in self.app.card_queue({'limit': 10})['items']])
            result = self.app.review({'id': ids[0], 'quality': 4})
            self.assertEqual(result['interval'], 1)
            self.assertEqual(result['card']['id'], ids[0])
            self.assertFalse(result['card']['ready'])
            self.assertEqual(result['counts'], {'total': 12, 'due': 10})
            with self.assertRaises(AppError): self.app.review({'id': ids[0], 'quality': 4})
            self.assertEqual(self.app.db.execute("SELECT COUNT(*) FROM events WHERE kind='review'").fetchone()[0], 1)
            self.assertEqual(self.app.card_detail({'id': ids[0]})['repetitions'], 1)
            failed = self.app.review({'id': ids[1], 'quality': 1})
            self.assertEqual(failed['interval'], 0)
            self.assertEqual(failed['due'], (fixed+timedelta(minutes=10)).isoformat(timespec='seconds'))

    def test_payload_limits_and_compact_creation_preserve_legacy_consumers(self):
        large = self.card('大きい', source='内置原创', model=None, unrelated='x'*2_100_000)
        identity = self.app.store_cards([large])[0]
        detail = self.app.card_detail({'id': identity})
        self.assertNotIn('unrelated', detail)
        self.assertLess(len(json.dumps(self.app.card_queue({}), ensure_ascii=False).encode()), 2_000_000)
        self.assertEqual(len(self.app.cards({})[0]['unrelated']), 2_100_000)
        compact = self.app.add_card(self.card('小さい'), compact=True)
        self.assertEqual(compact['card']['word'], '小さい')
        self.assertEqual(compact['counts']['total'], 2)
        seed = self.app.card_seed({'compact': True})
        self.assertEqual(len(seed['generated']), 5)
        self.assertIsInstance(self.app.card_seed({}), list)
        for params in ({'limit': 0}, {'limit': 51}, {'before': True}, {'query': 'x'*101}):
            with self.assertRaises(AppError): self.app.cards_page(params)
        with self.assertRaises(AppError): self.app.card_queue({'limit': 11})

    def test_queue_response_stays_below_ipc_limit_with_maximal_valid_teaching_fields(self):
        cards = []
        for index in range(10):
            c = self.card(f'最大{index}', source='内置原创', model=None)
            for field in ('romaji', 'meaning', 'example', 'translation', 'mnemonic'):
                c[field] = '😀'*6000
            c['reading'] = 'あ'*6000
            cards.append(c)
        self.app.store_cards(cards)
        response = self.app.card_queue({'limit': 10})
        self.assertEqual(len(response['items']), 10)
        self.assertLess(len(json.dumps(response, ensure_ascii=False).encode()), 2_000_000)


if __name__ == '__main__': unittest.main()
