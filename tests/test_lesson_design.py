"""Daily-course depth, practice independence and non-destructive version upgrades."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from curriculum import SEEDS
from service import Service, AppError, validate_lesson


class LessonDesignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Service(self.temp.name)

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def legacy(self, source='内置原创'):
        d = copy.deepcopy(SEEDS[0])
        for key in ('design_version', 'objectives', 'sections', 'vocabulary', 'dialogue', 'production'):
            d.pop(key)
        d.update(day=1, source=source, model=None)
        d['questions'] = d['questions'][:3]
        d['examples'] = d['examples'][:3]
        return self.app.save('lesson', d)

    def test_all_seven_courses_have_independent_listening_and_complete_design(self):
        for number, seed in enumerate(SEEDS, 1):
            with self.subTest(number=number):
                validate_lesson(copy.deepcopy(seed), rich=True)
                shown = self.app.lesson({'day': number})
                self.assertEqual(shown['design_version'], 2)
                self.assertEqual(len(shown['questions']), 8)
                self.assertTrue(all('answer' not in q and 'explanation' not in q for q in shown['questions']))
                score = self.app.grade({'id': shown['id'], 'answers': [q['answer'] for q in seed['questions']]})
                self.assertEqual(score['score'], 100)
        self.assertEqual(self.app.stats()['lessons'], 7)
        self.assertNotEqual(SEEDS[6]['sections'], SEEDS[1]['sections'])
        self.assertFalse({q['prompt'] for q in SEEDS[6]['questions']} &
                         {q['prompt'] for s in SEEDS[:6] for q in s['questions']})

    def test_builtin_upgrade_adds_version_preserving_old_course_and_grade(self):
        old = self.legacy()
        self.app.grade({'id': old['id'], 'answers': [q['answer'] for q in old['questions']]})
        before = [tuple(r) for r in self.app.db.execute('SELECT * FROM events')]
        new = self.app.lesson({'day': 1})
        self.assertNotEqual(new['id'], old['id'])
        self.assertEqual(self.app.content(old['id']), old)
        self.assertEqual(len(self.app.lesson({'id': old['id']})['questions']), 3)
        self.assertEqual(before, [tuple(r) for r in self.app.db.execute('SELECT * FROM events')])
        self.assertEqual(self.app.lesson({'day': 1})['id'], new['id'])
        self.assertEqual(self.app.stats()['lessons'], 1)
        self.app.grade({'id': new['id'], 'answers': [q['answer'] for q in SEEDS[0]['questions']]})
        self.assertEqual(self.app.stats()['lessons'], 1)

    def test_legacy_ai_is_reused_until_explicit_regeneration(self):
        old = self.legacy('AI生成')
        with patch('service.generate', return_value=(copy.deepcopy(SEEDS[0]), 'fixture-model')) as gen:
            self.assertEqual(self.app.lesson({'day': 1, 'source': 'ai'})['id'], old['id'])
            gen.assert_not_called()
            new = self.app.lesson({'day': 1, 'source': 'ai', 'regenerate': True})
            self.assertEqual(gen.call_count, 1)
            self.assertNotEqual(new['id'], old['id'])
            self.assertEqual(new['model'], 'fixture-model')
            context = gen.call_args.args[1]
            self.assertEqual(context['lesson_design']['questions'], 8)
            self.assertNotIn('questions', context['teaching_outline'])
        self.assertEqual(self.app.content(old['id']), old)

    def test_cached_only_does_not_generate_or_fall_back_to_builtin(self):
        self.app.lesson({'day': 1, 'source': 'builtin'})
        before = self.app.history('lesson')
        with patch('service.generate') as gen:
            for day in (1, 8, 28):
                self.assertIsNone(self.app.lesson({'day': day, 'source': 'ai', 'cached_only': True}))
            gen.assert_not_called()
        self.assertEqual(self.app.history('lesson'), before)
        self.assertFalse(self.app.curriculum({})['courses'][0]['generated'])
        saved = self.legacy('AI生成')
        with patch('service.generate') as gen:
            result = self.app.lesson({'day': 1, 'source': 'ai', 'cached_only': True})
            self.assertEqual(result['id'], saved['id'])
            self.assertNotIn('answer', result['questions'][0])
            gen.assert_not_called()
        self.assertTrue(self.app.curriculum({})['courses'][0]['generated'])
        with self.assertRaises(AppError):
            self.app.lesson({'day': 29, 'source': 'ai', 'cached_only': True})
        with self.assertRaises(AppError):
            self.app.lesson({'day': 1, 'source': 'ai', 'cached_only': True, 'regenerate': True})

    def test_incomplete_ai_is_retried_once_and_only_valid_result_saved(self):
        bad = copy.deepcopy(SEEDS[0]); bad['questions'] = bad['questions'][:3]
        with patch('service.generate', side_effect=[(bad, 'fixture'), (copy.deepcopy(SEEDS[0]), 'revised')]) as gen:
            d = self.app.lesson({'day': 1, 'source': 'ai'})
            self.assertEqual(gen.call_count, 2)
            self.assertIn('revision_requirement', gen.call_args.args[1])
            self.assertEqual(d['model'], 'revised')
        self.assertEqual(len(self.app.history('lesson')), 1)

    def test_failed_regeneration_leaves_existing_content_untouched(self):
        old = self.legacy('AI生成')
        with patch('service.generate', return_value=({'title': 'incomplete'}, 'fixture')) as gen:
            with self.assertRaisesRegex(AppError, '两次'):
                self.app.lesson({'day': 1, 'source': 'ai', 'regenerate': True})
            self.assertEqual(gen.call_count, 2)
        self.assertEqual(self.app.history('lesson'), [old])

    def test_rejects_shallow_or_repetitive_design(self):
        def mutation(d, name):
            if name == 'short_section': d['sections'][0]['explanation'] = '一句话'
            elif name == 'vocab': d['vocabulary'][1] = copy.deepcopy(d['vocabulary'][0])
            elif name == 'examples': d['examples'][1] = copy.deepcopy(d['examples'][0])
            elif name == 'dialogue': d['dialogue']['lines'] = d['dialogue']['lines'][:2]
            elif name == 'production': d['production'][0]['checklist'] = []
            elif name == 'types':
                for q in d['questions']: q['task_type'] = 'recognition'
            elif name == 'prompts': d['questions'][1]['prompt'] = d['questions'][0]['prompt']
            elif name == 'options': d['questions'][0]['options'] = ['はい', ' はい。', 'いいえ']
            elif name == 'copied_audio': d['questions'][-1]['audio'] = d['examples'][0]['jp'] + '　'
            elif name == 'repeated_audio': d['questions'][-1]['audio'] = d['questions'][-2]['audio']
            elif name == 'leaked_audio': d['questions'][-1]['prompt'] += d['questions'][-1]['audio']
        for name in ('short_section', 'vocab', 'examples', 'dialogue', 'production', 'types', 'prompts', 'options', 'copied_audio', 'repeated_audio', 'leaked_audio'):
            with self.subTest(name=name):
                d = copy.deepcopy(SEEDS[0]); mutation(d, name)
                with self.assertRaises(AppError): validate_lesson(d, rich=True)

    def test_example_cloze_is_rejected_but_fixed_phrase_in_new_context_allowed(self):
        d = copy.deepcopy(SEEDS[1])
        d['questions'][0].update(prompt='选助词：わたし（　）会社員です。', options=['は', 'を', 'に'], answer=0)
        with self.assertRaisesRegex(AppError, '挖空'): validate_lesson(d, rich=True)
        validate_lesson(copy.deepcopy(SEEDS[0]), rich=True)


if __name__ == '__main__': unittest.main()
