"""Deterministic exam-quality regression tests; no remote calls."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from llm import AppError
from jlpt_quality import check_order, check_order_review, check_option_analysis, ordering_candidates, ORDER_EXAMPLE
from study_generation import fingerprint
import test_study_generation as fixtures
from test_study_generation import generation_fixture


class QualityContracts(unittest.TestCase):
    def test_same_sorting_frame_is_not_duplicate_but_option_shuffle_is(self):
        first=dict(ORDER_EXAMPLE)
        shuffled=dict(first,options=list(reversed(first['options'])))
        different=dict(first,options=['作った','ケーキ','母が','昨日'])
        self.assertEqual(fingerprint(first),fingerprint(shuffled))
        self.assertNotEqual(fingerprint(first),fingerprint(different))

    def test_all_four_fragments_are_used_and_star_is_a_slot(self):
        check_order(ORDER_EXAMPLE,require_sentence=True)
        out=ordering_candidates(ORDER_EXAMPLE)
        self.assertEqual((out['slot_count'],out['star_position']),(4,3))
        self.assertEqual(len(out['candidates']),24)
        self.assertEqual({c['star_option'] for c in out['candidates']},{0,1,2,3})
        self.assertNotIn('answer',out)
        for candidate in out['candidates']:
            self.assertEqual(sorted(candidate['solution_order']),[0,1,2,3])
            self.assertEqual(candidate['star_option'],candidate['solution_order'][2])

    def test_regression_fragment_omission_and_wrong_star_index(self):
        for update in (dict(solution_order=[0,1,1,3]), dict(solution_order=[True,2,0,3]),
                       dict(answer=1), dict(completed_sentence='これは本です。'),
                       dict(prompt='これは ＿＿ が ＿＿ ★ ＿＿ です。')):
            with self.subTest(update=update),self.assertRaises(AppError):
                check_order(dict(ORDER_EXAMPLE,**update),require_sentence=True)

    def test_punctuation_mismatch_returns_actual_reconstruction_without_relaxing_check(self):
        q=dict(ORDER_EXAMPLE,completed_sentence='このかばんは、あのかばんより、少し大きいです。')
        with self.assertRaisesRegex(AppError,'程序实际拼接为：このかばんは、あのかばん より 少し 大きい です。'):
            check_order(q,require_sentence=True)
        q=dict(ORDER_EXAMPLE,options=['大きい','です。','より','少し'])
        with self.assertRaisesRegex(AppError,'です。。'):
            check_order(q,require_sentence=True)

    def test_two_plausible_options_are_not_accepted(self):
        checks=[dict(option='ます',fits=True,reason='未来陈述'),dict(option='たいです',fits=True,reason='愿望也成立')]
        with self.assertRaisesRegex(AppError,'唯一正解'):
            check_option_analysis(['ます','たいです'],0,checks)

    def test_order_review_requires_complete_matrix_and_consistent_unique_star(self):
        q=ORDER_EXAMPLE
        checks=[dict(solution_order=c['solution_order'],valid=c['solution_order']==q['solution_order'],reason='测试判定')
                for c in ordering_candidates(q)['candidates']]
        review=dict(answer=q['answer'],solution_order=q['solution_order'],possible_star_options=[q['answer']],
                    permutation_checks=checks)
        check_order_review(q,review)
        same_star=copy.deepcopy(checks)
        next(c for c in same_star if c['solution_order'][2]==q['answer'] and not c['valid'])['valid']=True
        check_order_review(q,dict(review,permutation_checks=same_star))
        for bad in (checks[:-1], checks[:-1]+[checks[0]], []):
            with self.subTest(checks=len(bad)),self.assertRaises(AppError):
                check_order_review(q,dict(review,permutation_checks=bad))
        alternate=copy.deepcopy(checks)
        next(c for c in alternate if c['solution_order'][2]!=q['answer'])['valid']=True
        with self.assertRaisesRegex(AppError,'未支持唯一'):
            check_order_review(q,dict(review,permutation_checks=alternate))


class QualityIntegration(unittest.TestCase):
    setUp=fixtures.GenerationTests.setUp
    tearDown=fixtures.GenerationTests.tearDown
    start=fixtures.GenerationTests.start
    count_papers=fixtures.GenerationTests.count_papers
    complete=fixtures.GenerationTests.complete
    ready_step=fixtures.GenerationTests.ready_step

    def test_explanation_edit_preserves_question_and_requires_fresh_global_review(self):
        globals_seen=[];edit_inputs=[];authors=[]
        def model(task,context,schema):
            role=context['_jlpt_role']
            if role=='explanation_editor':
                edit_inputs.append(copy.deepcopy(context))
                self.assertEqual(self.count_papers(),0)
                self.assertNotIn('explanation_repairs',str(self.app.study_generation_status({'id':job['id']})))
                return dict(edits=[dict(id=q['id'],can_edit=True,explanation='修正后的中文解析。',reason='')
                                   for q in context['questions']]),'fixture-editor'
            if role=='author':authors.append(context)
            result,identity=generation_fixture(task,context,schema)
            if role=='global_reviewer':
                globals_seen.append(copy.deepcopy(context))
                if len(globals_seen)==1:
                    result.update(approved=False,issues=[dict(question_ids=['q2'],scope='explanation',
                        reason='仅解析语言不符，题目和答案正确。',material_issue=False)])
            return result,identity
        job=self.start()
        with patch('service.generate',side_effect=model):done=self.complete(job)
        self.assertEqual(len(authors),2)
        self.assertEqual(len(edit_inputs),1)
        self.assertEqual(len(globals_seen),2)
        before=globals_seen[0]['questions'];after=globals_seen[1]['questions']
        for a,b in zip(before,after):
            self.assertEqual({k:v for k,v in a.items() if k!='explanation'},
                             {k:v for k,v in b.items() if k!='explanation'})
        self.assertEqual(after[1]['explanation'],'修正后的中文解析。')
        self.assertEqual(done['retry_count'],0)
        paper=self.app._paper(done['paper_id'])
        self.assertEqual(paper['generation']['explanation_edits']['q2']['model'],'fixture-editor')

    def test_editor_cannot_change_key_and_cancelled_editor_cannot_save(self):
        job=self.start()
        with patch('service.generate',side_effect=generation_fixture):
            while job['phase']!='global_review':job=self.ready_step(job)
        stored=self.app._generation(job['id'])
        stored['explanation_repairs']=[dict(id='q1',issues='解析语言')]
        original=copy.deepcopy(stored['segments'][0]['questions'])
        with self.app.db:self.app._save_generation(stored)
        bad=dict(edits=[dict(id='q1',can_edit=True,explanation='改写',reason='',answer=1)])
        with patch('service.generate',return_value=(bad,'editor')):self.ready_step(job)
        self.assertEqual(self.app._generation(job['id'])['segments'][0]['questions'],original)
        self.assertEqual(self.count_papers(),0)
        def cancel(*args):
            self.app.study_generation_cancel({'id':job['id']})
            return dict(edits=[dict(id='q1',can_edit=True,explanation='改写',reason='')]),'editor'
        with patch('service.generate',side_effect=cancel):progress=self.ready_step(job)
        self.assertEqual(progress['status'],'cancelled')
        self.assertEqual(self.count_papers(),0)
        self.assertNotIn('explanation_repairs',self.app._generation(job['id']))

    def test_editor_can_escalate_real_content_error_to_reauthor(self):
        job=self.start()
        with patch('service.generate',side_effect=generation_fixture):
            while job['phase']!='global_review':job=self.ready_step(job)
        stored=self.app._generation(job['id'])
        stored['explanation_repairs']=[dict(id='q1',issues='解析错误')]
        with self.app.db:self.app._save_generation(stored)
        result=dict(edits=[dict(id='q1',can_edit=False,explanation='',reason='两个选项均成立')])
        with patch('service.generate',return_value=(result,'editor')):self.ready_step(job)
        stored=self.app._generation(job['id'])
        self.assertEqual(stored['segments'][0]['status'],'pending')
        self.assertEqual(stored['segments'][0]['retry_ids'],['q1'])
        self.assertNotEqual(stored['global_review']['status'],'approved')
        self.assertEqual(self.count_papers(),0)

    def test_content_issue_overrides_explanation_edit_for_same_question(self):
        job=self.start()
        with patch('service.generate',side_effect=generation_fixture):
            while job['phase']!='global_review':job=self.ready_step(job)
        result=dict(approved=False,summary='需要修复',issues=[
            dict(question_ids=['q2'],scope='explanation',reason='解析语言不符'),
            dict(question_ids=['q2'],scope='question',reason='题面有两个答案')])
        with patch('service.generate',return_value=(result,'reviewer')):self.ready_step(job)
        stored=self.app._generation(job['id'])
        self.assertFalse(stored['explanation_repairs'])
        self.assertEqual(stored['segments'][0]['retry_ids'],['q2'])
        self.assertEqual(self.count_papers(),0)

    def test_reauthor_discards_stale_explanation_edit_provenance(self):
        job=self.app._generation(self.start()['id']);segment=job['segments'][0]
        with patch('service.generate',side_effect=generation_fixture):self.app._author_segment(job,segment)
        segment['question_explanation_edits']={'q1':dict(model='old-editor')}
        self.app._queue_generation_repair(job,segment,['q1'],[dict(id='q1',issues='题面错误')])
        with patch('service.generate',side_effect=generation_fixture):self.app._author_segment(job,segment)
        self.assertNotIn('q1',segment['question_explanation_edits'])

    def test_global_ordering_contract_checks_final_permutations_not_author_schema(self):
        job=self.app.study_generation_start(dict(level='N5',mode='targeted',type_id='grammar_order',count=5))
        with patch('service.generate',side_effect=generation_fixture) as model:done=self.complete(job)
        context=next(c.args[1] for c in model.call_args_list if c.args[1]['_jlpt_role']=='global_reviewer')
        self.assertIn('不是考生题面的必填字段',context['quality_contract']['ordering'])
        self.assertNotIn('example',context['question_types'][0]['quality_contract'])
        paper=self.app._paper(done['paper_id'])
        for q in paper['questions']:
            self.assertNotIn('solution_order',q)
            self.assertEqual(context['ordering_candidates'][q['id']],ordering_candidates(q))

    def test_author_sees_other_types_for_cross_type_duplicate_prevention(self):
        job=self.app._generation(self.start()['id']);segment=job['segments'][0]
        other=dict(type_id='paraphrase',questions=[dict(id='q-other',prompt='別の文です。',options=['別','異','多','少'])])
        job['segments'].append(other)
        with patch('service.generate',side_effect=generation_fixture) as model:self.app._author_segment(job,segment)
        self.assertEqual(model.call_args.args[1]['previous_questions'],[
            dict(other['questions'][0],type_id='paraphrase')])

    def test_author_evidence_is_removed_from_blind_review_and_public_progress(self):
        observed=[]
        def model(task,context,schema):
            observed.append(copy.deepcopy(context))
            return generation_fixture(task,context,schema)
        job=self.start()
        with patch('service.generate',side_effect=model):
            first=self.app.study_generation_step({'id':job['id']})
            self.app.study_generation_step({'id':job['id']})
        self.assertEqual(observed[0]['_jlpt_role'],'author')
        self.assertEqual(observed[1]['_jlpt_role'],'reviewer')
        for q in observed[1]['questions']:
            self.assertFalse({'answer','explanation','option_checks','solution_order'} & q.keys())
        self.assertNotIn('option_checks',str(first))

    def test_false_approval_with_multiple_fits_still_rejects_only_affected_question(self):
        job=self.start()
        with patch('service.generate',side_effect=generation_fixture):self.app.study_generation_step({'id':job['id']})
        def bad_reviewer(task,context,schema):
            result,model=generation_fixture(task,context,schema)
            result['reviews'][1]['option_checks'][1]['fits']=True
            return result,model
        with patch('service.generate',side_effect=bad_reviewer):
            progress=self.app.study_generation_step({'id':job['id']})
        stored=self.app._generation(job['id'])['segments'][0]
        self.assertEqual(stored['retry_ids'],['q2'])
        self.assertEqual(progress['completed_questions'],0)
        self.assertEqual(self.count_papers(),0)
        self.assertIn('reviewer_explanation',stored['retry_feedback'][0])
        self.assertNotIn('reviewer_explanation',str(progress))

    def test_repair_history_is_bounded_and_drives_new_strategy(self):
        job=self.app._generation(self.start()['id']);segment=job['segments'][0]
        accepted=[self.app._queue_generation_repair(job,segment,['q1'],[dict(id='q1',issues=str(i))])
                  for i in range(5)]
        self.assertEqual(accepted,[True,True,True,True,False])
        self.assertEqual(len(segment['repair_history']),3)
        self.assertEqual(segment['repair_history'][0]['feedback'][0]['issues'],'1')
        with patch('service.generate',side_effect=generation_fixture) as model:
            self.app._author_segment(job,segment)
        self.assertIn('更换考点',model.call_args.args[1]['repair_strategy'])

    def test_self_contradictory_review_retries_original_without_reauthoring(self):
        job=self.start()
        with patch('service.generate',side_effect=generation_fixture):self.app.study_generation_step({'id':job['id']})
        original=copy.deepcopy(self.app._generation(job['id'])['segments'][0]['questions'])
        def contradiction(task,context,schema):
            result,model=generation_fixture(task,context,schema)
            for c in result['reviews'][0]['option_checks']:c['fits']=False
            return result,model
        with patch('service.generate',side_effect=contradiction):
            progress=self.app.study_generation_step({'id':job['id']})
        stored=self.app._generation(job['id'])['segments'][0]
        self.assertEqual(progress['phase'],'reviewing')
        self.assertEqual(progress['retry_count'],0)
        self.assertEqual(stored['questions'],original)
        self.assertEqual(stored['review_format_retries'],1)
        self.assertEqual(self.count_papers(),0)


if __name__=='__main__':unittest.main()
