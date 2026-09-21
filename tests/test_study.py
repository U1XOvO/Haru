"""Real SQLite session lifecycle and strict AI/import contracts, isolated from user data."""
import copy
import json
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from service import Service
from study import LEVELS, SKILLS, load_data, validate_paper
from llm import AppError
from test_study_generation import generation_fixture


def fixture(level='N5', count=5, skill='mixed'):
    kinds=list(SKILLS) if skill=='mixed' else [skill]
    return dict(title='离线测试模拟题',level=level,approved=True,questions=[dict(prompt=f'测试题{i+1}',
        options=['一','二','三','四'],answer=i%4,explanation='测试解析',skill=kinds[i%len(kinds)],
        passage='図書館は月曜日が休みです。' if kinds[i%len(kinds)]=='reading' else '',
        audio_text='明日は九時に会いましょう。' if kinds[i%len(kinds)]=='listening' else '',grammar_ids=[]) for i in range(count)])


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.app=Service(self.temp.name)
    def tearDown(self):self.app.close();self.temp.cleanup()
    def start(self,mode='practice'):
        return self.app.study_start(dict(id='official-2018-N5',mode=mode))
    def submit(self,a,correct=True):
        raw=self.app._attempt(a['id'])
        answers={q['id']:q['answer'] if correct else (q['answer']+1)%len(q['options']) for q in raw['paper']['questions']}
        return self.app.study_save(dict(id=a['id'],revision=a['revision'],answers=answers,finish=True))

    def test_content_integrity_all_levels(self):
        grammar=self.app.grammar_items();ids={g['id'] for g in grammar};self.assertEqual(len(ids),100)
        for lv in LEVELS:
            gs=[g for g in grammar if g['level']==lv];self.assertEqual(len(gs),20);self.assertEqual(len({g['unit'] for g in gs}),4)
            for g in gs:
                self.assertEqual(len(g['questions']),3);self.assertTrue(set(g['related_ids'])<=ids)
                validate_paper(dict(title=g['title'],level=lv,sections=[dict(id='grammar',title='语法',seconds=600)],questions=g['questions']),ids)
        papers=load_data('papers.json');self.assertEqual(len(papers),10)
        self.assertEqual(sum(len(p['questions']) for p in papers),1001)
        for p in papers:
            validate_paper(p,ids)
            self.assertIsNone(p['exam_year']);self.assertIn(p['publication_year'],(2012,2018))
            self.assertTrue(all(q['answer_source']['url'].endswith('answer.pdf') for q in p['questions']))
    def test_grammar_catalog_reads_static_catalog_once_per_service(self):
        with patch('study.load_data',wraps=load_data) as read:
            self.app.grammar_catalog({'level':'N5'})
            self.app.grammar_catalog({'level':'N4'})
        self.assertEqual([c.args[0] for c in read.call_args_list if c.args[0]=='grammar.json'],['grammar.json'])
    def test_official_key_known_samples(self):
        p=self.app._paper('official-2018-N5');qs=p['questions']
        self.assertEqual([q['answer']+1 for q in qs[:12]],[4,1,3,1,3,4,1,4,2,3,2,2])
        self.assertEqual(len(qs),91);self.assertEqual(qs[-1]['answer']+1,2)
        for lv in ('N1','N2'):
            qs=self.app._paper('official-2018-'+lv)['questions']
            self.assertEqual([q['locator'].split(' · ')[-1] for q in qs[-4:]],['1','2','3(1)','3(2)'])
        audio=next(r for r in self.app._paper('official-2012-N5')['resources'] if r['title']=='听力問題2音频')
        self.assertEqual(audio['url'],'https://www.jlpt.jp/samples/sample2017/mp3/N5Q2.mp3')
    def test_no_answers_or_official_solution_before_submit(self):
        a=self.start('timed')
        for q in a['paper']['questions']:
            self.assertNotIn('answer',q);self.assertNotIn('explanation',q)
            self.assertNotIn('answer_source',q)
        self.assertFalse(any(r['kind'] in ('answer','script') for r in a['paper']['resources']))
    def test_partial_save_reopen_and_flags(self):
        a=self.start();q=a['paper']['questions'][0]
        saved=self.app.study_save(dict(id=a['id'],revision=0,answers={q['id']:2},flags=[q['id']]))
        self.app.close();self.app=Service(self.temp.name)
        fresh=self.app.study_attempt({'id':a['id']});self.assertEqual(fresh['answers'],{q['id']:2});self.assertEqual(fresh['flags'],[q['id']])
        self.assertEqual(saved['revision'],fresh['revision'])
    def test_stale_save_cannot_overwrite(self):
        a=self.start();qid=a['paper']['questions'][0]['id']
        self.app.study_save(dict(id=a['id'],revision=0,answers={qid:1}))
        with self.assertRaises(AppError):self.app.study_save(dict(id=a['id'],revision=0,answers={qid:2}))
        self.assertEqual(self.app._attempt(a['id'])['answers'][qid],1)
    def test_invalid_answer_batch_is_atomic(self):
        a=self.start();qs=a['paper']['questions']
        for bad in (True,5,-1,'1'):
            with self.assertRaises(AppError):self.app.study_save(dict(id=a['id'],revision=0,answers={qs[0]['id']:1,qs[1]['id']:bad}))
            self.assertEqual(self.app._attempt(a['id'])['answers'],{})
    def test_submit_unanswered_and_duplicate(self):
        a=self.start();r=self.app.study_save(dict(id=a['id'],revision=0,finish=True))
        self.assertEqual(r['result']['unanswered'],91);self.assertEqual(r['result']['correct'],0)
        same=self.app.study_save(dict(id=a['id'],revision=0,answers={'nonsense':1},finish=True))
        self.assertEqual(same['result'],r['result']);self.assertEqual(self.app.study_summary({})['submitted'],1)
    def test_concurrent_submit_counts_once(self):
        a=self.app.grammar_practice({'id':'n5-001'})
        def submit(_):
            app=Service(self.temp.name)
            try:return app.study_save(dict(id=a['id'],revision=0,finish=True))
            finally:app.close()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(submit,range(2)))
        self.assertEqual(results[0]['result'],results[1]['result'])
        self.assertEqual(self.app.grammar_detail({'id':'n5-001'})['progress']['attempts'],1)
    def test_read_favorite_does_not_mark_mastered(self):
        self.app.grammar_mark({'id':'n1-001'});self.app.grammar_mark({'id':'n1-001','favorite':True})
        self.app.grammar_mark({'id':'n1-001'})
        s=self.app.study_summary({});self.assertEqual(s['read'],1);self.assertEqual(s['mastered'],0)
        self.assertEqual(self.app.progression()['stage'],1)
    def test_grammar_results_review_and_old_course_isolation(self):
        a=self.app.grammar_practice({'id':'n1-001'});self.submit(a)
        d=self.app.grammar_detail({'id':'n1-001'})['progress'];self.assertTrue(d['mastered']);self.assertIn('due',d)
        self.assertEqual(self.app.stats()['lessons'],0);self.assertEqual(self.app.stats()['attempts'],0)
        self.assertEqual(self.app.progression()['stage'],1)
    def test_wrong_only_retry_does_not_master_whole_point(self):
        a=self.app.grammar_practice({'id':'n5-001'});self.submit(a,False)
        retry=self.app.study_retry({'id':a['id'],'wrong_only':True});self.assertNotIn('grammar_id',retry['paper']);self.submit(retry)
        self.assertFalse(self.app.grammar_detail({'id':'n5-001'})['progress']['mastered'])
        self.assertEqual(self.app.study_mistakes({'level':'N5'}),[])
    def test_timed_section_isolation_and_advance(self):
        a=self.start('timed');nextq=next(q for q in a['paper']['questions'] if q['section']=='s2')
        with self.assertRaises(AppError):self.app.study_save(dict(id=a['id'],revision=0,answers={nextq['id']:1}))
        a=self.app.study_save(dict(id=a['id'],revision=0,next_section=True));self.assertEqual(a['section_index'],1)
        q=a['paper']['questions'][0]
        with self.assertRaises(AppError):self.app.study_save(dict(id=a['id'],revision=a['revision'],answers={q['id']:1}))
    def test_expiration_survives_process_restart_and_uses_saved_answers(self):
        a=self.start('timed');q=a['paper']['questions'][0]
        a=self.app.study_save(dict(id=a['id'],revision=0,answers={q['id']:3}))
        with patch('study.time.time',return_value=a['deadline']+10000):
            result=self.app.study_attempt({'id':a['id']})
        self.assertEqual(result['status'],'submitted');self.assertEqual(result['result']['correct'],1)
    def test_snapshot_and_multiple_attempts(self):
        a=self.start();b=self.start();self.assertNotEqual(a['id'],b['id'])
        self.submit(a);self.assertEqual(self.app._attempt(b['id'])['status'],'active')
    def test_specialist_filter(self):
        a=self.app.study_start(dict(id='official-2018-N5',skill='reading'))
        self.assertEqual(len(a['paper']['questions']),6);self.assertTrue(all(q['skill']=='reading' for q in a['paper']['questions']))
    def test_generation_all_levels_and_model_provenance(self):
        for lv in LEVELS:
            with patch('service.generate',side_effect=generation_fixture) as gen:
                result=self.app.study_generate(dict(level=lv,count=5,skill='mixed'))
                self.assertEqual(gen.call_args.args[1]['level'],lv)
            p=self.app._paper(result['id']);self.assertEqual(p['model'],'fixture-author');self.assertEqual(p['source_type'],'ai')
            self.assertEqual(gen.call_count,9)
            self.assertEqual(len({q['answer'] for q in p['questions']}),4)
    def test_review_failure_does_not_save_draft(self):
        def reject_reviews(task,context,schema):
            result,model=generation_fixture(task,context,schema)
            if 'questions' in context:
                result['approved']=False
                for review in result['reviews']:
                    review.update(approved=False,issues='fixture 拒绝')
            return result,model
        with patch('service.generate',side_effect=reject_reviews):
            with self.assertRaises(AppError):self.app.study_generate(dict(level='N5',count=5))
        self.assertEqual(self.app.db.execute('SELECT COUNT(*) FROM study_papers').fetchone()[0],0)
    def test_legacy_generation_request_validation(self):
        for request in (dict(level='N6',count=5),dict(level='N5',count=4),
                        dict(level='N5',count=5,skill='invalid')):
            with self.assertRaises(AppError):self.app.study_generate(request)
        self.assertEqual(self.app.db.execute('SELECT COUNT(*) FROM study_papers').fetchone()[0],0)
    def test_api_error_no_fake_fallback(self):
        with patch('service.generate',side_effect=AppError('断网')):
            with self.assertRaises(AppError):self.app.study_generate(dict(level='N2',count=5))
        self.assertEqual(self.app.db.execute('SELECT COUNT(*) FROM study_papers').fetchone()[0],0)
    def test_import_validates_and_strips_untrusted_metadata(self):
        d=fixture();d.update(source='本地原创',sections=[dict(id='s',title='练习',seconds=600)],source_type='official',model='spoof')
        for q in d['questions']:q['section']='s';q['answer_html']='<script>bad</script>'
        out=self.app.study_import({'paper':d});p=self.app._paper(out['id'])
        self.assertEqual(p['source_type'],'import');self.assertNotIn('model',p);self.assertNotIn('answer_html',p['questions'][0])
    def test_import_missing_audio_asset_atomic(self):
        d=fixture();d.update(source='本地',sections=[dict(id='s',title='练习',seconds=600)])
        for q in d['questions']:q['section']='s'
        d['questions'][0]['audio_asset']='a'*32+'.mp3'
        with self.assertRaises(AppError):self.app.study_import({'paper':d})
        self.assertEqual(self.app.db.execute('SELECT COUNT(*) FROM study_papers').fetchone()[0],0)
    def test_asset_traversal(self):
        for x in ('../../.env','a'*32+'.json','https://evil.test/x.pdf'):
            with self.assertRaises(AppError):self.app.study_asset_path(x)
    def test_export_contains_study_and_no_config_secrets(self):
        a=self.app.grammar_practice({'id':'n5-001'});self.submit(a)
        raw=Path(self.app.export({})['path']).read_text();d=json.loads(raw)
        self.assertEqual(len(d['study']['attempts']),1);self.assertEqual(len(d['study']['grammar_progress']),1)
        self.assertNotIn('LLM_API_KEY',raw)
    def test_existing_v3_migration_backup_once(self):
        self.app.lesson({'day':1});self.app.card_seed({})
        with self.app.db:self.app.db.execute("DELETE FROM kv WHERE key='study_schema'")
        self.app.close();self.app=Service(self.temp.name)
        self.assertEqual(len(list((Path(self.temp.name)/'backups').glob('before-study-*.sqlite3'))),1)
        self.assertEqual(len(self.app.cards({})),5)
        self.app.close();self.app=Service(self.temp.name)
        self.assertEqual(len(list((Path(self.temp.name)/'backups').glob('before-study-*.sqlite3'))),1)


if __name__=='__main__':unittest.main()
