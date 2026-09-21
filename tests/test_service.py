import copy
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from curriculum import SEEDS
from llm import AppError, public_config
from service import Service, validate_lesson, validate_questions

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.app=Service(self.temp.name)
    def tearDown(self): self.app.close(); self.temp.cleanup()
    def lesson(self,day=1): return self.app.lesson({'day':day})
    def complete(self,l,wrong=False):
        d=self.app.content(l['id']);answers=[(q['answer']+int(wrong))%len(q['options']) for q in d['questions']]
        return self.app.grade({'id':l['id'],'answers':answers})
    def test_empty_progress_is_real(self):
        s=self.app.stats();self.assertEqual((s['lessons'],s['cards'],s['streak'],s['attempts']),(0,0,0,0))
        with self.assertRaises(AppError): self.app.quiz({})
    def test_builtin_lessons_validated(self):
        for seed in SEEDS: validate_lesson(copy.deepcopy(seed))
    def test_answers_hidden_before_submit(self):
        l=self.lesson();self.assertNotIn('answer',l['questions'][0]);self.assertNotIn('explanation',l['questions'][0])
        self.assertNotIn('answer',self.app.list_history({'kind':'lesson'})[0]['questions'][0])
    def test_course_cache(self): self.assertEqual(self.lesson()['id'],self.lesson()['id'])
    def test_grade_and_duplicate_submission(self):
        l=self.lesson();self.assertEqual(self.complete(l)['score'],100);self.assertFalse(self.complete(l)['recorded']);self.assertEqual(self.app.stats()['lessons'],1)
    def test_different_variant_does_not_inflate_days(self):
        l=self.lesson();self.complete(l)
        other=self.app.save('lesson',dict(SEEDS[0],day=1,source='AI生成',model='test'))
        self.complete(other);self.assertEqual(self.app.stats()['lessons'],1);self.assertEqual(self.app.stats()['next_day'],2)
    def test_future_course_does_not_skip_beginning(self):
        self.complete(self.lesson(3));self.assertEqual(self.app.stats()['next_day'],1)
    def test_invalid_answers_no_progress(self):
        l=self.lesson()
        for a in ([],[True]*len(l['questions']),[9]*len(l['questions'])):
            with self.assertRaises(AppError): self.app.grade({'id':l['id'],'answers':a})
        self.assertEqual(self.app.stats()['lessons'],0)
    def test_wrong_answers_saved(self):
        r=self.complete(self.lesson(),True);self.assertEqual(r['score'],0);self.assertTrue(self.app.get('mistakes'))
    def test_week_boundary_and_only_completed_content(self):
        self.lesson(2);l=self.lesson();self.complete(l)
        q=self.app.quiz({'source':'builtin'});self.assertEqual(q['scope'],[l['title']])
        past=(date.today()-timedelta(days=date.today().weekday()+1)).isoformat()+'T12:00:00'
        with self.app.db:self.app.db.execute('UPDATE events SET created=?',(past,))
        with self.assertRaises(AppError):self.app.quiz({'source':'builtin'})
    def test_card_deduplication(self):
        self.app.card_seed({});self.app.card_seed({});self.assertEqual(len(self.app.cards({})),5)
    def test_srs_success_intervals_and_repeat_guard(self):
        c=self.app.card_seed({})[0]
        for interval in (1,6,15):
            with self.app.db:self.app.db.execute('UPDATE cards SET due=? WHERE id=?',('2000-01-01',c['id']))
            r=self.app.review({'id':c['id'],'quality':4});self.assertEqual(r['interval'],interval)
        with self.assertRaises(AppError):self.app.review({'id':c['id'],'quality':4})
    def test_srs_failure_returns_in_ten_minutes(self):
        c=self.app.card_seed({})[0];r=self.app.review({'id':c['id'],'quality':1})
        delta=datetime.fromisoformat(r['due'])-datetime.now();self.assertTrue(590<delta.total_seconds()<=600)
        self.assertEqual(r['interval'],0)
    def test_invalid_card_quality_not_saved(self):
        c=self.app.card_seed({})[0]
        with self.assertRaises(AppError):self.app.review({'id':c['id'],'quality':6})
        self.assertEqual(self.app.stats()['today'],0)
    def test_ai_failure_preserves_database(self):
        with patch('service.generate',side_effect=AppError('连接失败')):
            with self.assertRaises(AppError):self.app.lesson({'day':1,'source':'ai'})
        self.assertEqual(self.app.history('lesson'),[])
    def test_ai_malformed_response_not_saved(self):
        with patch('service.generate',return_value=({'title':'bad'},'mock')):
            with self.assertRaises(AppError):self.app.lesson({'day':1,'source':'ai'})
        self.assertFalse(self.app.history('lesson'))
    def test_quiz_source_must_match_learning_evidence(self):
        self.complete(self.lesson())
        q=copy.deepcopy(SEEDS[0]['questions'][0]);q['source_id']='unlearned'
        with patch('service.generate',return_value=({'title':'bad','questions':[q]},'mock')):
            with self.assertRaises(AppError):self.app.quiz({'source':'ai'})
    def test_duplicate_options_rejected(self):
        q=copy.deepcopy(SEEDS[0]['questions']);q[0]['options']=['x','x']
        with self.assertRaises(AppError):validate_questions(q)
    def test_listening_audio_required(self):
        q=copy.deepcopy(SEEDS[0]['questions']);q[-1]['audio']=''
        with self.assertRaises(AppError):validate_questions(q)
    def test_vocabulary_category_normalizes_to_combined_bucket(self):
        q=copy.deepcopy(SEEDS[0]['questions']);q[0]['skill']='vocabulary';validate_questions(q);self.assertEqual(q[0]['skill'],'grammar')
    def test_profile_validation_and_persistence(self):
        with self.assertRaises(AppError):self.app.profile({'name':'我','minutes':20,'time':'25:00','goal':'旅行'})
        self.assertEqual(self.app.get('profile')['time'],'20:30')
        self.app.profile({'name':'小林','minutes':15,'time':'19:30','goal':'旅行日语','romaji':False})
        other=Service(self.temp.name);self.assertEqual(other.get('profile')['name'],'小林');other.close()
    def test_export_excludes_config(self):
        p=Path(self.app.export({})['path']);d=json.loads(p.read_text());self.assertNotIn('config',d);self.assertNotIn('LLM_API_KEY',p.read_text())
    def test_allowlisted_actions(self):
        with self.assertRaises(AppError):self.app.route('shell',{})
    def test_config_never_returns_key(self):
        c=public_config();self.assertEqual(set(c),{'configured','model','provider'})
if __name__=='__main__':unittest.main(verbosity=2)
