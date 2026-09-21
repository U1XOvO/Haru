"""Isolated SQLite/fixture tests; no model or teaching-quality claims."""
import copy
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from llm import AppError
from service import Service
from jlpt_blueprints import get_blueprint, LEVELS
from study_generation import make_segments, classify_sanitized_generation_error
from jlpt_quality import reconstruct


def generation_fixture(task, context, schema):
    """Contract fixture shared by browser tests, visibly unlike real exam data."""
    if context.get('review_scope')=='global':
        return dict(approved=True,summary='离线全局审查 fixture：整卷一致性通过。',issues=[]),'fixture-global-reviewer'
    if 'questions' in context:
        assert all('answer' not in q and 'explanation' not in q for q in context['questions'])
        reviews=[]
        for q in context['questions']:
            r=dict(id=q['id'],answer=0,approved=True,
                   explanation='离线审题 fixture：正解与其余测试选项不同。',issues='')
            if q['type_id']=='grammar_order':
                r.update(solution_order=[1,2,0,3],possible_star_options=[0])
                r['permutation_checks']=[dict(solution_order=c['solution_order'],valid=c['solution_order']==[1,2,0,3],
                    reason='离线排列判断 fixture') for c in context['ordering_candidates'][q['id']]['candidates']]
            else:r['option_checks']=[dict(option=o,fits=i==0,reason='离线逐项检查') for i,o in enumerate(q['options'])]
            reviews.append(r)
        return dict(approved=True,reviews=reviews), 'fixture-reviewer'
    plan=context['question_plan']; materials=[]
    for mid in dict.fromkeys(p['material_id'] for p in plan):
        materials.append(dict(id=mid,
            passage=f'离线材料 {mid}。図書館は月曜日が休みです。' if context['material']=='passage' else '',
            audio_text=f'离线音频 {mid}。明日は九時に会いましょう。' if context['material']=='audio_text' else ''))
    questions=[dict(id=p['id'],type_id=p['type_id'],
        skill=p['skill'],material_id=p['material_id'],prompt=f"离线题目 {p['id']} {p['type_id']}【测试】" +
        (' ＿＿ ＿＿ ★ ＿＿' if p['type_id']=='grammar_order' else ''),
        options=['正解','誤答甲','誤答乙','誤答丙'][:p['options_count']],answer=0,
        explanation='离线命题解析',grammar_ids=[]) for p in plan]
    for q in questions:
        if q['type_id']=='grammar_order':
            q.update(solution_order=[1,2,0,3],completed_sentence=reconstruct(q['prompt'],q['options'],[1,2,0,3]))
        else:q['option_checks']=[dict(option=o,fits=i==0,reason='离线逐项检查') for i,o in enumerate(q['options'])]
    return dict(level=context['level'],materials=materials,questions=questions), 'fixture-author'


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.app=Service(self.temp.name)
    def tearDown(self):
        self.app.close();self.temp.cleanup()
    def start(self, **kwargs):
        return self.app.study_generation_start(dict(level='N5',mode='targeted',type_id='kanji_reading',count=5,**kwargs))
    def complete(self, job):
        for _ in range(150):
            if job['status'] in ('complete','cancelled','failed'): break
            job=self.ready_step(job)
        self.assertEqual(job['status'],'complete',job.get('error'))
        return job
    def ready_step(self, job):
        retry_at=self.app._generation(job['id']).get('retry_at',0)
        with patch('study_generation.time.time',return_value=max(time.time(),retry_at+1)):
            return self.app.study_generation_step({'id':job['id']})
    def count_papers(self):
        return self.app.db.execute('SELECT COUNT(*) FROM study_papers').fetchone()[0]
    def test_sanitized_provider_errors_fail_closed(self):
        self.assertEqual(classify_sanitized_generation_error('API 鉴权失败，请检查 .env 中的密钥及账户权限。'),'configuration')
        self.assertEqual(classify_sanitized_generation_error('API 请求失败（HTTP 400），请检查模型名称与接口兼容性。'),'configuration')
        self.assertEqual(classify_sanitized_generation_error('API 请求失败（HTTP 503），请检查模型名称与接口兼容性。'),'transient')
        self.assertEqual(classify_sanitized_generation_error('网络连接失败或超时。'),'transient')
        self.assertEqual(classify_sanitized_generation_error('unrecognized sanitized error'),'permanent')
    def test_all_levels_full_exact_blueprint_and_blind_review(self):
        with patch('service.generate',side_effect=generation_fixture) as mock:
            for lv in LEVELS:
                job=self.app.study_generation_start(dict(level=lv,mode='full'))
                self.assertEqual(job['completed_questions'],0)
                total=job['total_segments'];before=mock.call_count
                complete=self.complete(job)
                self.assertEqual(mock.call_count-before,total*2+1)
                paper=self.app._paper(complete['paper_id']);bp=get_blueprint(lv)
                self.assertEqual(len(paper['questions']),bp['count'])
                self.assertEqual(paper['sections'],bp['sections'])
                self.assertEqual(paper['generation_model'],'fixture-author')
                self.assertEqual(paper['review_model'],'fixture-reviewer')
                self.assertEqual(paper['global_review_model'],'fixture-global-reviewer')
                self.assertTrue(paper['generation']['global_review']['approved'])
                for t in bp['types']:
                    qs=[q for q in paper['questions'] if q['type_id']==t['id']]
                    self.assertEqual(len(qs),t['count'])
                    self.assertEqual([len(q['options']) for q in qs],t['options_counts'])
                    if t['material']:
                        self.assertTrue(all(q[t['material']] for q in qs))
                attempt=self.app.study_start({'id':paper['id']})
                self.assertTrue(all('answer' not in q and 'explanation' not in q for q in attempt['paper']['questions']))
                self.assertNotIn('questions',json.loads(self.app.db.execute('SELECT data FROM study_generation_jobs WHERE id=?',(job['id'],)).fetchone()[0])['segments'][0])
                graded=self.app.study_save(dict(id=attempt['id'],revision=0,
                    answers={q['id']:q['answer'] for q in paper['questions']},finish=True))
                self.assertEqual(graded['result']['correct'],bp['count'])
    def test_shared_units_never_split(self):
        for lv in LEVELS:
            blueprint=get_blueprint(lv);segments=make_segments(blueprint,'full')
            owners={}
            for i,s in enumerate(segments):
                for q in s['plan']:
                    self.assertIn(owners.setdefault(q['material_id'],i),(i,))
        grammar=next(s for s in make_segments(get_blueprint('N5'),'full') if s['type_id']=='grammar_text')
        self.assertEqual(grammar['count'],5)
    def test_targeted_five_ten_and_shared_material(self):
        with patch('service.generate',side_effect=generation_fixture):
            for count in (5,10):
                job=self.app.study_generation_start(dict(level='N3',mode='targeted',type_id='reading_medium',count=count))
                paper=self.app._paper(self.complete(job)['paper_id'])
                self.assertEqual(len(paper['questions']),count)
                self.assertEqual({q['type_id'] for q in paper['questions']},{'reading_medium'})
                self.assertEqual({q['section'] for q in paper['questions']},{'practice'})
    def test_public_progress_resume_and_idempotent_finish(self):
        job=self.start()
        with patch('service.generate',side_effect=generation_fixture):
            a=self.app.study_generation_step({'id':job['id']})
            self.assertEqual((a['phase'],a['completed_questions']),('reviewing',0))
            self.assertNotIn('正解',json.dumps(a,ensure_ascii=False))
            self.app.close();self.app=Service(self.temp.name)
            self.assertEqual(self.app.study_catalog({'level':'N1'})['generation']['id'],job['id'])
            b=self.app.study_generation_step({'id':job['id']})
            self.assertEqual(b['completed_questions'],3)
            self.assertEqual(self.count_papers(),0)
            done=self.complete(b)
            self.assertEqual(self.app.study_generation_step({'id':job['id']})['paper_id'],done['paper_id'])
            self.assertEqual(self.count_papers(),1)
    def test_invalid_input_and_only_one_pending_job(self):
        for p in [dict(level='N6'),dict(level='N5',mode='targeted',type_id='reading_long',count=5),
                  dict(level='N5',mode='targeted',type_id='kanji_reading',count=True)]:
            with self.assertRaises(AppError):self.app.study_generation_start(p)
        job=self.start()
        with self.assertRaises(AppError):self.start()
        self.app.study_generation_cancel({'id':job['id']})
        self.assertIsNone(self.app.generation_pending())
        self.assertEqual(self.app.study_generation_step({'id':job['id']})['status'],'cancelled')
    def test_network_failure_keeps_reviewable_draft(self):
        job=self.start()
        with patch('service.generate',side_effect=generation_fixture):self.app.study_generation_step({'id':job['id']})
        with patch('service.generate',side_effect=AppError('网络连接失败或超时。')):
            failed=self.app.study_generation_step({'id':job['id']})
        self.assertEqual((failed['status'],failed['phase']),('active','reviewing'))
        self.assertGreater(failed['retry_after'],0)
        self.assertEqual(self.count_papers(),0)
        with patch('service.generate',side_effect=generation_fixture):
            resumed=self.ready_step(failed)
            self.complete(resumed)
    def test_reviewer_disagreement_automatically_rewrites_only_failed_question(self):
        job=self.start()
        with patch('service.generate',side_effect=generation_fixture):
            self.app.study_generation_step({'id':job['id']});self.app.study_generation_step({'id':job['id']})
            self.app.study_generation_step({'id':job['id']})
        def disagree(task,context,schema):
            d,m=generation_fixture(task,context,schema);d['reviews'][0]['answer']=1;return d,m
        with patch('service.generate',side_effect=disagree):failed=self.app.study_generation_step({'id':job['id']})
        self.assertEqual((failed['status'],failed['phase'],failed['completed_questions']),('active','generating',3))
        self.assertEqual((failed['retry_count'],failed['retry_question_count']), (1,1))
        self.assertEqual(failed['error'],'');self.assertTrue(failed['retrying'])
        self.assertEqual(self.count_papers(),0)
        with patch('service.generate',side_effect=generation_fixture) as model:
            self.complete(self.app.study_generation_step({'id':job['id']}))
        self.assertEqual([p['id'] for p in model.call_args_list[0].args[1]['question_plan']],['q4'])

    def test_multiple_review_rejections_complete_without_manual_resume(self):
        job=self.start();calls=[];rejections=0;original=None
        def generate(task,context,schema):
            nonlocal rejections,original
            d,m=generation_fixture(task,context,schema)
            if 'questions' in context and rejections<2:
                rejections+=1;d['approved']=False
                next(r for r in d['reviews'] if r['id']=='q2').update(approved=False,issues='测试歧义反馈，不应出现在公开进度。')
            elif 'questions' not in context:
                calls.append([p['id'] for p in context['question_plan']])
                if original is None:original=copy.deepcopy(d['questions'])
                if context.get('retry_feedback'):
                    self.assertEqual(context['retry_feedback'][0]['id'],'q2')
                    self.assertEqual([q['id'] for q in context['retained_questions']],['q1','q3'])
                    d['questions'][0]['prompt']+=' 修订'+str(rejections)
            return d,m
        with patch('service.generate',side_effect=generate):
            complete=self.complete(job)
        self.assertEqual(calls,[['q1','q2','q3'],['q2'],['q2'],['q4','q5']])
        self.assertEqual(complete['retry_count'],2);self.assertFalse(complete['retrying'])
        paper=self.app._paper(complete['paper_id'])
        self.assertEqual(paper['questions'][0]['prompt'],original[0]['prompt'])
        self.assertEqual(paper['questions'][2]['prompt'],original[2]['prompt'])
        self.assertEqual(paper['generation']['retry_count'],2)

    def test_retry_state_survives_restart_without_exposing_feedback(self):
        job=self.start()
        with patch('service.generate',side_effect=generation_fixture):self.app.study_generation_step({'id':job['id']})
        def reject(task,context,schema):
            d,m=generation_fixture(task,context,schema);d['approved']=False
            d['reviews'][1].update(approved=False,issues='答案是正解，隐藏这段审题反馈。');return d,m
        with patch('service.generate',side_effect=reject):retry=self.app.study_generation_step({'id':job['id']})
        self.assertNotIn('答案是',json.dumps(retry,ensure_ascii=False));self.assertNotIn('正解',json.dumps(retry,ensure_ascii=False))
        self.app.close();self.app=Service(self.temp.name)
        persisted=self.app.generation_pending()
        self.assertEqual((persisted['status'],persisted['retry_count'],persisted['retry_question_count']),('active',1,1))
        with patch('service.generate',side_effect=generation_fixture) as model:self.complete(persisted)
        self.assertEqual(model.call_args_list[0].args[1]['count'],1)

    def test_shared_material_locked_when_only_one_question_is_rejected(self):
        job=self.app.study_generation_start(dict(level='N3',mode='targeted',type_id='reading_medium',count=5))
        with patch('service.generate',side_effect=generation_fixture):self.app.study_generation_step({'id':job['id']})
        def reject(task,context,schema):
            d,m=generation_fixture(task,context,schema);d['approved']=False;d['reviews'][1]['approved']=False;return d,m
        with patch('service.generate',side_effect=reject):self.app.study_generation_step({'id':job['id']})
        original=copy.deepcopy(self.app._generation(job['id'])['segments'][0]['questions'])
        def change_material(task,context,schema):
            d,m=generation_fixture(task,context,schema)
            self.assertEqual(len(context['fixed_materials']),1)
            d['materials'][0]['passage']='変更された文章です。';return d,m
        with patch('service.generate',side_effect=change_material):retry=self.app.study_generation_step({'id':job['id']})
        self.assertEqual(retry['status'],'active');self.assertEqual(retry['retry_count'],2)
        self.assertEqual(retry['retry_question_count'],3)
        self.assertEqual(self.app._generation(job['id'])['segments'][0]['questions'],original)
        with patch('service.generate',side_effect=generation_fixture):self.complete(retry)

    def test_bad_shared_material_rewrites_related_group_together(self):
        job=self.app.study_generation_start(dict(level='N3',mode='targeted',type_id='reading_medium',count=5))
        with patch('service.generate',side_effect=generation_fixture):self.app.study_generation_step({'id':job['id']})
        def reject(task,context,schema):
            d,m=generation_fixture(task,context,schema);d['approved']=False
            d['reviews'][1].update(approved=False,material_issue=True,issues='共同文章缺少必要信息。');return d,m
        with patch('service.generate',side_effect=reject):retry=self.app.study_generation_step({'id':job['id']})
        self.assertEqual(retry['retry_question_count'],3)
        with patch('service.generate',side_effect=generation_fixture) as model:self.complete(retry)
        self.assertEqual([p['id'] for p in model.call_args_list[0].args[1]['question_plan']],['q1','q2','q3'])
        self.assertEqual(model.call_args_list[0].args[1]['fixed_materials'],[])

    def test_invalid_review_and_invalid_replacement_keep_retrying(self):
        job=self.start()
        with patch('service.generate',side_effect=generation_fixture):self.app.study_generation_step({'id':job['id']})
        with patch('service.generate',return_value=({'approved':False},'invalid-reviewer')):
            retry=self.app.study_generation_step({'id':job['id']})
        self.assertEqual((retry['status'],retry['retry_question_count']),('active',3))
        def invalid(task,context,schema):
            d,m=generation_fixture(task,context,schema);d['questions'][0]['options']=['重复']*4;return d,m
        with patch('service.generate',side_effect=invalid):retry=self.app.study_generation_step({'id':job['id']})
        self.assertEqual((retry['status'],retry['retry_count']),('active',2))
        self.assertEqual(retry['retry_question_count'],1)
        self.assertEqual(self.count_papers(),0)
        with patch('service.generate',side_effect=generation_fixture) as model:self.complete(retry)
        self.assertEqual([q['id'] for q in model.call_args_list[0].args[1]['question_plan']],['q1'])
        self.assertEqual([q['id'] for q in model.call_args_list[1].args[1]['questions']],['q1','q2','q3'])

    def test_duplicate_options_rewrite_only_bad_question_and_review_all_after_restart(self):
        job=self.start();authors=[];reviews=[]
        def generate(task,context,schema):
            d,m=generation_fixture(task,context,schema)
            if context.get('review_scope')=='global':return d,m
            if 'question_plan' in context and 'questions' not in context:
                authors.append([p['id'] for p in context['question_plan']])
                if len(authors)==1:
                    # Also catch visually identical full/half-width and spacing.
                    d['questions'][1]['options']=['Ａ Ｂ',' A\u3000B ','違う','別']
                if context.get('retry_feedback'):
                    self.assertEqual([q['id'] for q in context['retained_questions']],['q1','q3'])
                    d['questions'][0]['prompt']+=' 修订'
                m='fixture-author-'+str(len(authors))
            else:
                reviews.append([q['id'] for q in context['questions']])
            return d,m
        with patch('service.generate',side_effect=generate):retry=self.app.study_generation_step({'id':job['id']})
        self.assertEqual((retry['status'],retry['phase'],retry['retry_question_count']),('active','generating',1))
        self.assertEqual(retry['error'],'');self.assertEqual(self.count_papers(),0)
        self.assertNotIn('Ａ',json.dumps(retry,ensure_ascii=False))
        segment=self.app._generation(job['id'])['segments'][0]
        preserved=copy.deepcopy(segment['questions'])
        self.assertEqual([q['id'] for q in preserved],['q1','q3'])
        self.assertEqual(segment['pending_review_ids'],['q1','q2','q3'])
        self.app.close();self.app=Service(self.temp.name)
        with patch('service.generate',side_effect=AppError('网络连接失败或超时。')):
            failed=self.app.study_generation_step({'id':job['id']})
        self.assertEqual(failed['status'],'active')
        self.assertEqual(self.app._generation(job['id'])['segments'][0]['questions'],preserved)
        with patch('service.generate',side_effect=generate):done=self.complete(self.app.study_generation_step({'id':job['id']}))
        self.assertEqual(authors,[['q1','q2','q3'],['q2'],['q4','q5']])
        self.assertEqual(reviews,[['q1','q2','q3'],['q4','q5']])
        paper=self.app._paper(done['paper_id'])
        self.assertEqual(paper['questions'][0]['prompt'],preserved[0]['prompt'])
        self.assertEqual(paper['questions'][2]['prompt'],preserved[1]['prompt'])
        stored=self.app._generation(job['id'])['segments'][0]
        self.assertEqual(stored['question_models']['q1']['model'],'fixture-author-1')
        self.assertEqual(stored['question_models']['q2']['model'],'fixture-author-2')
        self.assertEqual(set(stored['question_review_models']),{'q1','q2','q3'})

    def test_all_then_partial_duplicate_options_keep_retrying_and_review_retained_questions(self):
        job=self.start();authors=[];reviews=[]
        def generate(task,context,schema):
            d,m=generation_fixture(task,context,schema)
            if context.get('review_scope')=='global':return d,m
            if 'questions' in context:
                reviews.append([q['id'] for q in context['questions']])
                if len(reviews)==1:
                    # A retained sibling must still be rejected when its first
                    # independent review disagrees with the author's answer.
                    d['reviews'][1].update(approved=False,issues='保留题仍需要修正。');d['approved']=False
            else:
                authors.append([p['id'] for p in context['question_plan']])
                for q in d['questions']:
                    if len(authors)==1 or (len(authors)==2 and q['id']=='q1'):
                        q['options']=['重复']*len(q['options'])
            return d,m
        with patch('service.generate',side_effect=generate):done=self.complete(job)
        self.assertEqual(authors,[['q1','q2','q3'],['q1','q2','q3'],['q1'],['q2'],['q4','q5']])
        self.assertEqual(reviews,[['q1','q2','q3'],['q2'],['q4','q5']])
        self.assertEqual(done['retry_count'],3);self.assertEqual(self.count_papers(),1)

    def test_duplicate_options_preserve_shared_material_and_review_every_question(self):
        job=self.app.study_generation_start(dict(level='N3',mode='targeted',type_id='reading_medium',count=5))
        authors=[];reviews=[]
        def generate(task,context,schema):
            d,m=generation_fixture(task,context,schema)
            if context.get('review_scope')=='global':return d,m
            if 'questions' in context:reviews.append([q['id'] for q in context['questions']])
            else:
                authors.append([p['id'] for p in context['question_plan']])
                if len(authors)==1:d['questions'][1]['options']=['重复']*4
                if len(authors)==2:
                    self.assertEqual(len(context['fixed_materials']),1)
                    self.assertEqual(d['materials'],context['fixed_materials'])
            return d,m
        with patch('service.generate',side_effect=generate):done=self.complete(job)
        self.assertEqual(authors,[['q1','q2','q3'],['q2'],['q4','q5']])
        self.assertEqual(reviews,[['q1','q2','q3'],['q4','q5']])
        paper=self.app._paper(done['paper_id'])
        self.assertEqual(len({q['passage'] for q in paper['questions'][:3]}),1)

    def test_cancel_automatic_retry_prevents_more_model_calls(self):
        job=self.start()
        with patch('service.generate',side_effect=generation_fixture):self.app.study_generation_step({'id':job['id']})
        with patch('service.generate',return_value=({'approved':False},'reviewer')):
            self.app.study_generation_step({'id':job['id']})
        self.app.study_generation_cancel({'id':job['id']})
        with patch('service.generate') as model:
            result=self.app.study_generation_step({'id':job['id']});model.assert_not_called()
        self.assertEqual(result['status'],'cancelled');self.assertFalse(result['retrying']);self.assertEqual(self.count_papers(),0)

    def test_global_review_repairs_target_then_checks_entire_paper_again(self):
        job=self.start();global_contexts=[];authors=[];local_reviews=[];final_options=None
        def generate(task,context,schema):
            nonlocal final_options
            d,m=generation_fixture(task,context,schema)
            if context.get('review_scope')=='global':
                global_contexts.append(copy.deepcopy(context))
                self.assertEqual(self.count_papers(),0)
                if len(global_contexts)==1:
                    d.update(approved=False,issues=[dict(question_ids=['q2'],reason='全局发现与q4考点过于重复。',material_issue=False)])
                else:final_options=[q['options'] for q in context['questions']]
            elif 'questions' in context:local_reviews.append([q['id'] for q in context['questions']])
            else:
                authors.append([q['id'] for q in context['question_plan']])
                if context.get('retry_feedback'):
                    self.assertIn('全局审查',context['retry_feedback'][0]['issues'])
                    self.assertEqual(context['retry_feedback'][0]['reviewed_question']['options'],
                                     global_contexts[0]['questions'][1]['options'])
                    d['questions'][0]['prompt']+=' 全局修订'
            return d,m
        with patch('service.generate',side_effect=generate):done=self.complete(job)
        self.assertEqual(authors,[['q1','q2','q3'],['q4','q5'],['q2']])
        self.assertEqual(local_reviews,[['q1','q2','q3'],['q4','q5'],['q2']])
        self.assertEqual([len(c['questions']) for c in global_contexts],[5,5])
        self.assertEqual(done['global_review']['status'],'approved')
        self.assertEqual(done['global_review']['round'],2);self.assertEqual(done['global_revision_count'],1)
        paper=self.app._paper(done['paper_id'])
        self.assertEqual([q['options'] for q in paper['questions']],final_options)
        self.assertEqual(paper['generation']['global_review']['revision_count'],1)
        self.assertIn('全局修订',paper['questions'][1]['prompt'])

    def test_global_review_missing_or_unknown_issue_ids_retries_without_publication(self):
        job=self.start();rounds=0
        def generate(task,context,schema):
            nonlocal rounds
            if context.get('review_scope')=='global':
                rounds+=1
                if rounds==1:return dict(approved=False,summary='发现问题',issues=[]),'global-invalid'
                if rounds==2:return dict(approved=False,summary='发现问题',issues=[dict(question_ids=['missing'],reason='问题')]),'global-invalid'
                self.assertIn('格式无效',context['previous_review_error'])
            return generation_fixture(task,context,schema)
        with patch('service.generate',side_effect=generate):done=self.complete(job)
        self.assertEqual(rounds,3);self.assertEqual(done['retry_count'],0)
        self.assertEqual(done['global_review']['round'],3);self.assertEqual(self.count_papers(),1)

    def test_global_review_network_failure_preserves_candidate_for_resume(self):
        job=self.start()
        with patch('service.generate',side_effect=generation_fixture):
            while job['phase']!='global_review':job=self.app.study_generation_step({'id':job['id']})
        with patch('service.generate',side_effect=AppError('网络连接失败或超时。')):
            failed=self.app.study_generation_step({'id':job['id']})
        self.assertEqual(failed['status'],'active');self.assertEqual(failed['phase'],'global_review')
        candidate=copy.deepcopy(self.app._generation(job['id'])['candidate'])
        self.app.close();self.app=Service(self.temp.name)
        with patch('service.generate',side_effect=generation_fixture):
            reviewed=self.ready_step(failed)
            self.assertEqual(reviewed['phase'],'assembling');self.assertEqual(self.count_papers(),0)
            done=self.complete(reviewed)
        self.assertEqual(self.app._paper(done['paper_id'])['questions'],candidate['questions'])

    def test_cancel_during_global_review_cannot_publish(self):
        job=self.start()
        with patch('service.generate',side_effect=generation_fixture):
            while job['phase']!='global_review':job=self.app.study_generation_step({'id':job['id']})
        def cancel(task,context,schema):
            other=Service(self.temp.name)
            try:other.study_generation_cancel({'id':job['id']})
            finally:other.close()
            return generation_fixture(task,context,schema)
        with patch('service.generate',side_effect=cancel):out=self.app.study_generation_step({'id':job['id']})
        self.assertEqual(out['status'],'cancelled');self.assertEqual(self.count_papers(),0)
        self.assertNotIn('candidate',self.app._generation(job['id']))

    def test_global_material_issue_repairs_entire_shared_group(self):
        job=self.app.study_generation_start(dict(level='N3',mode='targeted',type_id='reading_medium',count=5))
        global_rounds=0;authors=[]
        def generate(task,context,schema):
            nonlocal global_rounds
            d,m=generation_fixture(task,context,schema)
            if context.get('review_scope')=='global':
                global_rounds+=1
                self.assertEqual(len(context['materials']),2)
                if global_rounds==1:d.update(approved=False,issues=[dict(question_ids=['q2'],reason='共享文章缺少关键信息',material_issue=True)])
            elif 'questions' not in context:authors.append([p['id'] for p in context['question_plan']])
            return d,m
        with patch('service.generate',side_effect=generate):self.complete(job)
        self.assertEqual(authors,[['q1','q2','q3'],['q4','q5'],['q1','q2','q3']])
    def test_malformed_generation_is_not_published(self):
        for mutate in [lambda d:d.update(level='N1'),lambda d:d['questions'].pop(),
                       lambda d:d['questions'][0].update(type_id='wrong'),
                       lambda d:d['questions'][0].update(answer=True)]:
            job=self.start()
            def broken(task,context,schema):
                d,m=generation_fixture(task,context,schema);mutate(d);return d,m
            with patch('service.generate',side_effect=broken):out=self.app.study_generation_step({'id':job['id']})
            self.assertEqual(out['status'],'active');self.assertEqual(out['retry_count'],1)
            self.assertEqual(self.count_papers(),0)
            self.app.study_generation_cancel({'id':job['id']})
    def test_ordering_requires_four_slots_and_one_star(self):
        for prompt in ('選んでください。★ ★', '＿＿ ★ ＿＿', '＿＿ ＿＿ ＿＿ ＿＿', '＿＿ ＿＿ ★ ＿＿ ＿＿'):
            job=self.app.study_generation_start(dict(level='N5',mode='targeted',type_id='grammar_order',count=5))
            def malformed(task,context,schema):
                d,m=generation_fixture(task,context,schema);d['questions'][0]['prompt']=prompt;return d,m
            with patch('service.generate',side_effect=malformed):out=self.app.study_generation_step({'id':job['id']})
            self.assertEqual(out['status'],'active');self.assertEqual(out['retry_count'],1);self.assertEqual(self.count_papers(),0)
            self.app.study_generation_cancel({'id':job['id']})

    def test_question_validation_errors_rewrite_only_affected_question_until_complete(self):
        mutations=[lambda q:q.update(options=['一','二']),lambda q:q.update(options='invalid'),
                   lambda q:q.update(answer=True),lambda q:q.update(prompt=''),
                   lambda q:q.update(type_id='wrong'),lambda q:q.update(grammar_ids=[{}]),
                   lambda q:q.update(audio_asset='not-an-asset'),lambda q:q.update(explanation=None)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                job=self.start();authors=[];reviews=[]
                def generate(task,context,schema):
                    d,m=generation_fixture(task,context,schema)
                    if context.get('review_scope')=='global':return d,m
                    if 'questions' in context:reviews.append([q['id'] for q in context['questions']])
                    else:
                        authors.append([q['id'] for q in context['question_plan']])
                        if len(authors)==1:mutate(d['questions'][1])
                    return d,m
                with patch('service.generate',side_effect=generate):done=self.complete(job)
                self.assertEqual(authors,[['q1','q2','q3'],['q2'],['q4','q5']])
                self.assertEqual(reviews,[['q1','q2','q3'],['q4','q5']])
                self.assertEqual(done['retry_count'],1)

    def test_segment_shape_errors_rewrite_segment_without_losing_approved_segments(self):
        for mutate in [lambda d:d.update(level='wrong'),lambda d:d['questions'].pop(),
                       lambda d:d.update(materials='invalid'),lambda d:d.update(questions=None)]:
            job=self.start()
            with patch('service.generate',side_effect=generation_fixture):
                self.app.study_generation_step({'id':job['id']});self.app.study_generation_step({'id':job['id']})
            first=copy.deepcopy(self.app._generation(job['id'])['segments'][0])
            def broken(task,context,schema):
                d,m=generation_fixture(task,context,schema);mutate(d);return d,m
            with patch('service.generate',side_effect=broken):retry=self.app.study_generation_step({'id':job['id']})
            self.assertEqual((retry['status'],retry['completed_questions']),('active',3))
            with patch('service.generate',side_effect=generation_fixture) as model:self.complete(retry)
            self.assertEqual([q['id'] for q in model.call_args_list[0].args[1]['question_plan']],['q4','q5'])
            stored=self.app._generation(job['id'])['segments'][0]
            self.assertEqual(stored['question_models'],first['question_models'])
            self.assertEqual(stored['question_review_models'],first['question_review_models'])

    def test_request_errors_backoff_stops_after_five_and_cancel_does_not_call_model(self):
        job=self.start();now=time.time()
        with patch('service.generate',side_effect=AppError('网络连接失败或超时。')) as model:
            for failure,delay in enumerate((2,4,8,16,32),1):
                with patch('study_generation.time.time',return_value=now):
                    job=self.app.study_generation_step({'id':job['id']})
                    self.assertEqual(job['recovery_count'],failure)
                    if failure<5:
                        self.assertEqual((job['status'],job['retry_after']),('active',delay))
                        self.assertEqual(job['error'],'')
                        self.app.study_generation_step({'id':job['id']})
                    else:
                        self.assertEqual((job['status'],job['retry_after'],job['failure_kind']),
                                         ('failed',0,'transient_limit'))
                        self.assertTrue(job['resume_allowed'])
                self.assertEqual(model.call_count,failure)
                now+=delay+1
        self.app.close();self.app=Service(self.temp.name)
        self.assertEqual(self.app.generation_pending()['recovery_count'],5)
        self.app.study_generation_cancel({'id':job['id']})
        with patch('service.generate') as model:
            cancelled=self.ready_step(job);model.assert_not_called()
        self.assertEqual(cancelled['status'],'cancelled');self.assertEqual(cancelled['retry_after'],0)

    def test_unexpected_processing_error_fails_closed_without_leaking_details(self):
        job=self.start()
        with patch.object(self.app,'_validate_segment',side_effect=RuntimeError('private internal exception')):
            with patch('service.generate',side_effect=generation_fixture):retry=self.app.study_generation_step({'id':job['id']})
        self.assertEqual(retry['status'],'failed');self.assertFalse(retry['resume_allowed'])
        self.assertEqual(retry['failure_kind'],'internal');self.assertEqual(retry['retry_after'],0)
        self.assertNotIn('private',json.dumps(retry));self.assertEqual(self.count_papers(),0)
        with patch('service.generate') as model:
            self.assertEqual(self.app.study_generation_step({'id':job['id']})['status'],'failed')
            model.assert_not_called()

    def test_sanitized_auth_failure_is_terminal_then_explicit_resume_preserves_approved_segment(self):
        job=self.start()
        with patch('service.generate',side_effect=generation_fixture):
            self.app.study_generation_step({'id':job['id']})
            self.app.study_generation_step({'id':job['id']})
        with patch('service.generate',side_effect=AppError('API 鉴权失败，请检查 .env 中的密钥及账户权限。')) as model:
            failed=self.app.study_generation_step({'id':job['id']})
            self.assertEqual(failed['status'],'failed')
            self.assertEqual(failed['failure_kind'],'configuration')
            self.assertTrue(failed['resume_allowed'])
            self.assertEqual(failed['retry_after'],0)
            self.assertEqual(self.app.study_generation_step({'id':job['id']})['status'],'failed')
            self.assertEqual(model.call_count,1)
        stored=self.app._generation(job['id'])
        self.assertEqual(stored['segments'][0]['status'],'approved')
        self.assertEqual(len(stored['segments'][0]['questions']),3)
        with patch('service.generate',side_effect=generation_fixture):
            resumed=self.app.study_generation_step({'id':job['id'],'resume':True})
            done=self.complete(resumed)
        self.assertEqual(done['status'],'complete')
        self.assertEqual(done['completed_questions'],5)

    def test_save_failure_rolls_back_and_retries_exact_reviewed_candidate(self):
        job=self.start()
        with patch('service.generate',side_effect=generation_fixture):
            while job['phase']!='assembling':job=self.app.study_generation_step({'id':job['id']})
        candidate=copy.deepcopy(self.app._generation(job['id'])['candidate']);save=self.app._save_generation
        def fail_once(value):
            if value['status']=='complete':raise AppError('模拟保存失败')
            return save(value)
        with patch.object(self.app,'_save_generation',side_effect=fail_once):
            retry=self.app.study_generation_step({'id':job['id']})
            self.assertEqual(retry['retry_after'],2)
            retry=self.ready_step(retry)
            self.assertEqual(retry['retry_after'],4)
        self.assertEqual((retry['status'],retry['phase']),('active','assembling'))
        self.assertEqual(self.count_papers(),0)
        self.assertEqual(self.app._generation(job['id'])['candidate'],candidate)
        with patch('service.generate') as model:
            done=self.complete(retry);model.assert_not_called()
        self.assertEqual(self.count_papers(),1)
        self.assertEqual(self.app._paper(done['paper_id'])['questions'],candidate['questions'])

    def test_assembly_duplicate_rewrites_only_later_question_and_repeats_reviews(self):
        job=self.start()
        with patch('service.generate',side_effect=generation_fixture):
            while job['phase']!='global_review':job=self.app.study_generation_step({'id':job['id']})
        raw=self.app._generation(job['id'])
        raw['segments'][1]['questions'][0]['prompt']=raw['segments'][0]['questions'][0]['prompt']
        with self.app.db:self.app._save_generation(raw)
        retry=self.app.study_generation_step({'id':job['id']})
        self.assertEqual((retry['status'],retry['retry_question_count'],retry['completed_questions']),('active',1,3))
        with patch('service.generate',side_effect=generation_fixture) as model:self.complete(retry)
        self.assertEqual([p['id'] for p in model.call_args_list[0].args[1]['question_plan']],['q4'])

    def test_start_request_id_is_idempotent_even_after_cancel(self):
        params=dict(level='N5',mode='targeted',type_id='kanji_reading',count=5,client_request_id='request-1234')
        first=self.app.study_generation_start(params)
        self.assertEqual(self.app.study_generation_start(params)['id'],first['id'])
        self.app.study_generation_cancel({'id':first['id']})
        self.assertEqual(self.app.study_generation_start(params)['status'],'cancelled')
        self.assertEqual(self.app.db.execute('SELECT COUNT(*) FROM study_generation_jobs').fetchone()[0],1)
    def test_cancel_during_api_and_duplicate_step_do_not_publish(self):
        job=self.start();entered=threading.Event();release=threading.Event();results=[]
        def blocking(task,context,schema):
            entered.set();release.wait(5);return generation_fixture(task,context,schema)
        def work():
            worker=Service(self.temp.name)
            try:results.append(worker.study_generation_step({'id':job['id']}))
            finally:worker.close()
        with patch('service.generate',side_effect=blocking) as mock:
            thread=threading.Thread(target=work);thread.start();self.assertTrue(entered.wait(5))
            duplicate=self.app.study_generation_step({'id':job['id']})
            self.assertTrue(duplicate['busy']);self.assertEqual(mock.call_count,1)
            self.app.study_generation_cancel({'id':job['id']});release.set();thread.join(5)
        self.assertEqual(results[0]['status'],'cancelled');self.assertEqual(self.count_papers(),0)
    def test_expired_lease_can_resume(self):
        job=self.start();raw=self.app._generation(job['id']);raw.update(lease='dead',lease_until=time.time()-1)
        with self.app.db:self.app._save_generation(raw)
        with patch('service.generate',side_effect=generation_fixture):out=self.app.study_generation_step({'id':job['id']})
        self.assertEqual(out['phase'],'reviewing')
    def test_delete_only_ai_and_history_snapshot_survives(self):
        with patch('service.generate',side_effect=generation_fixture):job=self.complete(self.start())
        attempt=self.app.study_start({'id':job['paper_id']})
        self.app.study_delete({'id':job['paper_id']})
        self.assertEqual(self.count_papers(),0)
        with self.assertRaises(AppError):self.app.study_start({'id':job['paper_id']})
        self.assertEqual(self.app.study_attempt({'id':attempt['id']})['paper']['id'],job['paper_id'])
        with self.assertRaises(AppError):self.app.study_delete({'id':'official-2018-N5'})
        with self.app.db:
            self.app.db.execute('INSERT INTO study_papers VALUES (?,?,?)',('import-test',json.dumps({'source_type':'import'}),time.time()))
        with self.assertRaises(AppError):self.app.study_delete({'id':'import-test'})


if __name__=='__main__':unittest.main()
