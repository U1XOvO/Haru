"""Publication-bound counts, genuine subtypes and inseparable material groups."""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from jlpt_blueprints import get_blueprint, get_blueprints
from llm import AppError


class BlueprintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.papers = json.loads((Path(__file__).resolve().parents[1] / 'data' / 'study' / 'papers.json').read_text())

    def test_counts_sections_and_options_preserve_published_answer_sheets(self):
        totals = {'N5': 91, 'N4': 98, 'N3': 102, 'N2': 107, 'N1': 107}
        for level, total in totals.items():
            with self.subTest(level=level):
                blueprint = get_blueprint(level, self.papers)
                source = next(p for p in self.papers if p['id'] == 'official-2018-' + level)
                self.assertEqual(blueprint['count'], total)
                self.assertEqual(blueprint['sections'], source['sections'])
                self.assertEqual(sum(t['count'] for t in blueprint['types']), total)
                self.assertEqual(len({t['id'] for t in blueprint['types']}), len(blueprint['types']))
                self.assertIn('不代表现行考试', blueprint['note'])
                self.assertEqual(blueprint['publication_year'], 2018)
                self.assertEqual([x for t in blueprint['types'] for x in t['source_question_ids']],
                                 [q['id'] for q in source['questions']])
                self.assertEqual([x for t in blueprint['types'] for x in t['options_counts']],
                                 [len(q['options']) for q in source['questions']])
                for subtype in blueprint['types']:
                    self.assertEqual(sum(subtype['units']), subtype['count'])
                    self.assertEqual(len(subtype['locators']), subtype['count'])
                    self.assertTrue(subtype['requirements'])
                    self.assertEqual(subtype['title'], subtype['name'])
                    self.assertNotIn('answer', subtype)

    def test_level_specific_subtypes_and_three_choice_listening(self):
        for level in ('N5', 'N4', 'N3', 'N2', 'N1'):
            types = {t['id']: t for t in get_blueprint(level)['types']}
            self.assertEqual('word_formation' in types, level == 'N2')
            self.assertEqual('orthography' in types, level != 'N1')
            self.assertEqual('word_usage' in types, level != 'N5')
            self.assertEqual('reading_integrated' in types, level in ('N1', 'N2'))
            self.assertEqual('listening_expression' in types, level in ('N5', 'N4', 'N3'))
            self.assertEqual(types['listening_response']['options_count'], 3)
            if 'listening_expression' in types:
                self.assertEqual(types['listening_expression']['options_count'], 3)
                self.assertIn('文字情景', types['listening_expression']['requirements'])
            self.assertEqual(types['grammar_order']['format'], 'ordering')
            self.assertIn('★', types['grammar_order']['requirements'])
            self.assertEqual(types['grammar_text']['material'], 'passage')
            self.assertEqual(types['grammar_text']['units'], [5])

    def test_long_shared_materials_and_integrated_listening_are_not_split(self):
        types4 = {t['id']: t for t in get_blueprint('N4')['types']}
        self.assertEqual(types4['reading_medium']['units'], [4])
        types3 = {t['id']: t for t in get_blueprint('N3')['types']}
        self.assertEqual(types3['reading_medium']['units'], [3, 3])
        self.assertEqual(types3['reading_long']['units'], [4])
        for level in ('N1', 'N2'):
            types = {t['id']: t for t in get_blueprint(level)['types']}
            self.assertEqual(types['reading_medium']['units'], [3, 3, 3])
            self.assertEqual(types['listening_integrated']['units'], [1, 1, 2])
            self.assertEqual([s.rsplit(' · ', 1)[-1] for s in types['listening_integrated']['locators']],
                             ['1', '2', '3(1)', '3(2)'])

    def test_invalid_level_missing_source_and_unknown_group_fail_closed(self):
        with self.assertRaises(AppError):
            get_blueprint('N6')
        with self.assertRaises(AppError):
            get_blueprint('N5', [])
        papers = copy.deepcopy(self.papers)
        next(p for p in papers if p['id'] == 'official-2018-N5')['questions'][0]['group'] = '問題99'
        with self.assertRaises(AppError):
            get_blueprint('N5', papers)

    def test_returns_independent_serializable_values(self):
        first = get_blueprint('N5', self.papers)
        first['sections'][0]['seconds'] = 0
        first['types'][0]['options_counts'][0] = 9
        self.assertEqual(get_blueprint('N5', self.papers)['sections'][0]['seconds'], 1500)
        self.assertEqual(get_blueprint('N5', self.papers)['types'][0]['options_counts'][0], 4)
        self.assertEqual(len(json.loads(json.dumps(get_blueprints()))), 5)


if __name__ == '__main__':
    unittest.main()
