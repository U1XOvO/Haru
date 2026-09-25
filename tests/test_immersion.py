"""Offline checks for the expanded immersion story contract."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from llm import AppError
from service import Service


class ImmersionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.app = Service(self.folder.name)
        self.addCleanup(self.app.close)

    def story(self, count):
        sentence = dict(jp='今日は喫茶店に行きました。', kana='きょうはきっさてんにいきました。',
                        romaji='Kyou wa kissaten ni ikimashita.', zh='今天去了咖啡店。')
        return dict(title='喫茶店の一日', sentences=[sentence.copy() for _ in range(count)],
                    words=[dict(word='喫茶店', meaning='咖啡店', reading='きっさてん')],
                    task='找一家附近的咖啡店。')

    def test_story_requires_at_least_eight_sentences_before_saving(self):
        with patch.object(self.app, 'ai', return_value=self.story(8)) as ai:
            result = self.app.immersion({'topic':'咖啡店'})
        self.assertEqual(len(result['sentences']), 8)
        prompt = ai.call_args.args[0]
        self.assertIn('原创沉浸式日语故事', prompt)
        self.assertIn('难度比当前阶段的常见练习略高', prompt)
        self.assertIn('避免流水账', prompt)
        self.assertEqual(ai.call_args.args[1]['immersion_story'], True)
        self.assertEqual(ai.call_args.args[1]['topic'], '咖啡店')
        with patch.object(self.app, 'ai', return_value=self.story(7)):
            with self.assertRaisesRegex(AppError, '故事篇幅不足'):
                self.app.immersion({'topic':'咖啡店'})
        self.assertEqual(len(self.app.history('immersion')), 1)


if __name__ == '__main__':
    unittest.main()
