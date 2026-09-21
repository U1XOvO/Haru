"""Stage boundaries, remedial gates, v1 preservation."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
from curriculum import SEEDS
from progression import stage_info, course_spec
from service import Service, AppError


class ProgressionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.app=Service(self.temp.name)
    def tearDown(self):
        self.app.close();self.temp.cleanup()
    def fixture(self, number, legacy=False):
        d=copy.deepcopy(SEEDS[(number-1)%len(SEEDS)])
        d.update(day=number,source='AI生成',model='test',title=f'测试课{number}')
        if not legacy:d.update(lesson_no=number,stage=course_spec(number)['stage'])
        for i,q in enumerate(d['questions']):q['prompt']=f'第{number}课第{i+1}题：'+q['prompt']
        return self.app.save('lesson',d)
    def submit(self,d,wrong=False):
        d=self.app.content(d['id'])
        return self.app.grade(dict(id=d['id'],answers=[(q['answer']+int(wrong))%len(q['options']) for q in d['questions']]))
    def finish(self,start=1,end=28,legacy=False):
        for n in range(start,end+1):self.submit(self.fixture(n,legacy))
    def pass_stage(self):return self.submit(self.app.stage_assessment({}))

    def test_initial_catalog_and_locked_future(self):
        p=self.app.bootstrap({});self.assertEqual(p['progression']['next_lesson'],1)
        self.assertEqual(len(p['curriculum']['courses']),28)
        for params in ({'day':29},{'lesson_no':29,'source':'ai'}):
            with self.assertRaises(AppError):self.app.lesson(params)
        with self.assertRaises(AppError):self.app.curriculum({'stage':2})
        with self.assertRaises(AppError):self.app.stage_assessment({})
        d=self.fixture(29)
        with self.assertRaises(AppError):self.app.lesson({'id':d['id']})

    def test_boundary_28_assessment_then_29_and_cache(self):
        self.finish(legacy=True)
        self.assertEqual(self.app.stats()['lessons'],28)
        self.assertIsNone(self.app.stats()['next_day'])
        self.assertEqual(self.app.progression()['status'],'assessment_due')
        q=self.app.stage_assessment({});self.assertEqual(q['id'],self.app.stage_assessment({})['id'])
        self.assertTrue(6<=len(q['questions'])<=12)
        self.assertNotIn('answer',q['questions'][0])
        self.assertNotIn('explanation',self.app.list_history({'kind':'stage_assessment'})[0]['questions'][0])
        skills={x['skill'] for x in q['questions']};self.assertEqual(skills,{'grammar','kana','listening'})
        self.assertTrue(all(1<=x['lesson_no']<=28 for x in q['questions']))
        self.assertGreater(q['coverage'],1)
        self.assertTrue(self.submit(q)['passed'])
        self.assertEqual(self.app.progression()['next_lesson'],29)
        with patch('service.generate',return_value=(copy.deepcopy(SEEDS[0]),'mock')) as gen:
            l=self.app.lesson({'lesson_no':29,'source':'ai'})
            self.assertEqual(l['stage'],2);self.assertEqual(gen.call_args.args[1]['stage'],2)
            self.assertEqual(self.app.lesson({'lesson_no':29,'source':'ai'})['id'],l['id'])
            self.assertEqual(gen.call_count,1)
        self.assertFalse(self.submit(q,True)['recorded'])
        self.assertEqual(self.app.progression()['stage'],2)

    def test_failure_requires_remedial_then_fresh_assessment(self):
        self.finish();q=self.app.stage_assessment({});self.assertFalse(self.submit(q,True)['passed'])
        self.assertEqual(self.app.progression()['status'],'review_required')
        self.assertFalse(self.submit(q)['passed']) # Cannot change first score.
        with self.assertRaises(AppError):self.app.stage_assessment({})
        r=self.app.remedial({});self.assertEqual(r['id'],self.app.remedial({})['id'])
        self.assertEqual(r['assessment_id'],q['id']);self.assertNotIn('answer',r['questions'][0])
        self.assertFalse(self.submit(r,True)['passed'])
        self.assertTrue(self.submit(r)['passed'])
        self.assertEqual(self.app.stats()['lessons'],28)
        self.assertEqual(self.app.progression()['status'],'reassessment_ready')
        q2=self.app.stage_assessment({});self.assertNotEqual(q2['id'],q['id']);self.assertEqual(q2['attempt'],2)
        self.assertNotEqual(q2['questions'],q['questions'])
        self.assertTrue(self.submit(q2)['passed'])
        self.assertEqual(self.app.stats()['next_lesson'],29)
        self.assertEqual(self.app.db.execute("SELECT COUNT(*) FROM events WHERE kind='remedial'").fetchone()[0],1)

    def test_per_skill_threshold_blocks_overall_pass(self):
        self.finish();q=self.app.stage_assessment({});d=self.app.content(q['id'])
        counts={s:sum(x['skill']==s for x in d['questions']) for s in ('kana','listening','grammar')}
        skill=min(counts,key=counts.get)
        self.assertEqual(counts[skill],2)
        answers=[(x['answer']+int(x['skill']==skill))%len(x['options']) for x in d['questions']]
        r=self.app.grade({'id':d['id'],'answers':answers})
        self.assertGreaterEqual(r['score'],80);self.assertFalse(r['passed'])
        self.assertEqual(self.app.progression()['stage'],1)

    def test_many_stages_and_old_course_review(self):
        for stage in range(1,7):
            info=stage_info(stage);self.finish(info['start'],info['end']);self.assertTrue(self.pass_stage()['passed'])
            self.assertEqual(self.app.progression()['stage'],stage+1)
        self.assertGreater(self.app.stats()['next_lesson'],98)
        self.assertEqual(self.app.curriculum({'stage':1})['courses'][0]['lesson_no'],1)
        with patch('service.generate',return_value=(copy.deepcopy(SEEDS[0]),'mock')) as gen:
            self.app.lesson({'lesson_no':2,'source':'ai','regenerate':True})
            self.assertEqual(gen.call_args.args[1]['stage'],1)
        self.assertEqual(course_spec(10000)['lesson_no'],10000)

    def test_progression_export_after_pass(self):
        self.finish();self.pass_stage()
        exported=json.loads(Path(self.app.export({'type':'json'})['path']).read_text())
        self.assertEqual(exported['progression']['stage'],2)
        self.assertEqual(len(exported['stage_passes']),1)

    def test_concurrent_assessment_and_remedial_creation_are_reused(self):
        self.finish()
        def request(kind):
            app=Service(self.temp.name)
            try:return app.route(kind,{})['id']
            finally:app.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids=list(pool.map(request,['stage_assessment']*2))
        self.assertEqual(ids[0],ids[1])
        self.submit({'id':ids[0]},True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids=list(pool.map(request,['remedial']*2))
        self.assertEqual(ids[0],ids[1])

    def test_old_database_backup_and_preservation(self):
        self.submit(self.fixture(1,legacy=True));self.app.card_seed({})
        with self.app.db:
            self.app.db.execute('DROP TABLE stage_passes')
            self.app.db.execute('DROP INDEX unique_stage_completion')
            self.app.db.execute('PRAGMA user_version=0')
        before={t:[tuple(r) for r in self.app.db.execute(f'SELECT * FROM {t}')] for t in ('content','events','kv','cards','messages')}
        self.app.close();self.app=Service(self.temp.name)
        backups=list((Path(self.temp.name)/'backups').glob('*.sqlite3'));self.assertEqual(len(backups),1)
        for t,rows in before.items():self.assertEqual(rows,[tuple(r) for r in self.app.db.execute(f'SELECT * FROM {t}')])
        # A connection context manages transactions; closing releases the Windows file handle.
        with closing(sqlite3.connect(backups[0])) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],1)
        self.app.close();self.app=Service(self.temp.name)
        self.assertEqual(len(list((Path(self.temp.name)/'backups').glob('*.sqlite3'))),1)
        self.assertEqual(self.app.progression()['next_lesson'],2)

if __name__=='__main__':unittest.main()
