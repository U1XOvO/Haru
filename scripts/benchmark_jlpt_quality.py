"""Opt-in live JLPT quality checks with a ten-round ceiling and isolated storage.

One round is a benchmark run after an engineering change, not one API request.
Generated questions and model reviews are evidence, not human quality ratings.
"""
import argparse
import copy
import json
import sys
import time
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from llm import AppError, public_config
from service import Service


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


class BenchService(Service):
    def ai(self, task, context, schema):
        if len(self.trace) >= self.max_calls:
            raise AppError('本轮 API 调用预算已用完。')
        entry = dict(task=task, context=copy.deepcopy(context), schema=copy.deepcopy(schema))
        self.trace.append(entry)
        started = time.monotonic()
        try:
            result = super().ai(task, context, schema)
            entry['result'] = result
            return result
        except AppError as error:
            entry['error'] = str(error)
            raise
        finally:
            entry['seconds'] = round(time.monotonic() - started, 2)
            save(self.trace_path, self.trace)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--round', type=int, required=True, choices=range(1, 11))
    parser.add_argument('--round-limit', type=int, default=10, choices=range(1,11))
    parser.add_argument('--label', required=True)
    parser.add_argument('--case', action='append', help='LEVEL:TYPE (repeatable)')
    parser.add_argument('--full-level', choices=['N5', 'N4', 'N3', 'N2', 'N1'])
    parser.add_argument('--resume-from', type=Path, help='Prior report with exactly one unfinished_job; use an isolated DB')
    parser.add_argument('--audit-from', type=Path, help='Re-audit exactly one previously completed paper without regenerating it')
    parser.add_argument('--recheck-local', action='store_true', help='With audit-from, also re-solve all segments blindly')
    parser.add_argument('--targeted', action='store_true', help='Use all five questions instead of about three')
    parser.add_argument('--max-revisions', type=int, default=2)
    parser.add_argument('--max-calls', type=int, default=40)
    parser.add_argument('--output', type=Path, default=ROOT / 'runtime/validation/jlpt-quality')
    args = parser.parse_args()
    if not 0 <= args.max_revisions <= 10 or not 1 <= args.max_calls <= 300:
        parser.error('max-revisions: 0..10; max-calls: 1..300')
    if args.round>args.round_limit:
        parser.error('本批次轮数超过已设上限')
    resumed=None
    audited=None
    if args.audit_from:
        prior=json.loads(args.audit_from.read_text())
        completed=[c['paper'] for c in prior['cases'] if c.get('status')=='complete' and c.get('paper')]
        if len(completed)!=1 or args.case or args.full_level or args.resume_from:
            parser.error('audit-from要求恰好一份已完成试卷，且不与case/full-level/resume-from混用')
        audited=copy.deepcopy(completed[0])
        if audited['generation_mode']=='targeted' and len(audited['questions']) not in (5,10):
            parser.error('audit-from重审专项须为5或10题')
    if args.recheck_local and not args.audit_from:
        parser.error('recheck-local须与audit-from一起使用')
    if args.resume_from:
        prior=json.loads(args.resume_from.read_text())
        unfinished=[c['unfinished_job'] for c in prior['cases'] if c.get('unfinished_job')]
        if len(unfinished)!=1 or args.case or args.full_level:
            parser.error('resume-from要求恰好一个未完成快照，且不与case/full-level混用')
        resumed=copy.deepcopy(unfinished[0])
    config = public_config()
    if config['model'] != 'deepseek-flash':
        parser.error('本次评测只允许项目配置的 deepseek-flash')
    args.output.mkdir(parents=True,exist_ok=True)
    batch_path=args.output/'batch.json'
    if batch_path.exists():
        if json.loads(batch_path.read_text())['round_limit']!=args.round_limit:
            parser.error('已有批次的轮数上限不可变更')
    else:
        save(batch_path,dict(round_limit=args.round_limit))
    folder = args.output / f'round-{args.round:02d}'
    folder.mkdir(parents=True, exist_ok=False)  # Never overwrite a completed or interrupted run.
    sources=folder/'sources';sources.mkdir()
    for relative in ('backend/llm.py','backend/study_generation.py','backend/jlpt_quality.py',
                     'backend/jlpt_blueprints.py','scripts/benchmark_jlpt_quality.py'):
        shutil.copyfile(ROOT/relative,sources/Path(relative).name)
    report = dict(round=args.round, label=args.label, started=datetime.now(timezone.utc).isoformat(),
                  config=config, max_calls=args.max_calls, max_revisions=args.max_revisions,
                  resume_from=str(args.resume_from) if args.resume_from else None,
                  audit_from=str(args.audit_from) if args.audit_from else None,local_recheck=args.recheck_local,cases=[])
    app = BenchService(folder / 'db')
    app.trace = []; app.trace_path = folder / 'calls.json'; app.max_calls = args.max_calls
    requests = ([dict(level=resumed['level'],mode=resumed['mode'],
                     **(dict(type_id=resumed['segments'][0]['type_id'],count=resumed['count'])
                        if resumed['mode']=='targeted' else {}))] if resumed else
                [dict(level=audited['level'],mode=audited['generation_mode'],
                      **(dict(type_id=audited['questions'][0]['type_id'],count=len(audited['questions']))
                         if audited['generation_mode']=='targeted' else {}))] if audited else
                [dict(level=args.full_level, mode='full')] if args.full_level else
                [dict(level=case.split(':')[0], type_id=case.split(':')[1], mode='targeted', count=5)
                 for case in (args.case or ['N5:grammar_order', 'N5:grammar_form', 'N5:paraphrase'])])
    try:
        for request in requests:
            if len(app.trace) >= args.max_calls: break
            start = len(app.trace)
            if resumed:
                job=resumed
                job.update(status='active',lease=None,lease_until=0,retry_at=0,consecutive_errors=0,error='')
                # Re-solve existing rejected drafts before spending another
                # author call. This is a blind review, never an approval bypass.
                rereview=[]
                for segment in job['segments']:
                    if (segment['status']=='pending' and segment.get('retry_ids') and
                        {q['id'] for q in segment.get('questions',[])}=={p['id'] for p in segment['plan']}):
                        segment['status']='drafted'
                        segment['pending_review_ids']=segment['retry_ids']
                        rereview.extend(segment['retry_ids'])
                with app.db:app._save_generation(job)
            else:
                public = app.study_generation_start(request)
                job = app._generation(public['id'])
                if audited:
                    previous=audited['generation']['segments']
                    assert len(previous)==len(job['segments']), '分段布局不一致，不能直接复用'
                    questions={q['id']:q for q in audited['questions']}
                    for segment,metadata in zip(job['segments'],previous):
                        assert all(segment[k]==metadata[k] for k in ('type_id','count')), '分段布局不一致'
                        segment.update(copy.deepcopy(metadata))
                        segment.update(status='approved',questions=[copy.deepcopy(questions[p['id']]) for p in segment['plan']])
                        segment['question_explanation_edits']={p['id']:copy.deepcopy(audited['generation']['explanation_edits'][p['id']])
                            for p in segment['plan'] if p['id'] in audited['generation'].get('explanation_edits',{})}
                    job['candidate']=audited
                    job['candidate']['generation']['job_id']=job['id']
                    if args.recheck_local:
                        for segment in job['segments']:
                            segment.update(status='drafted',pending_review_ids=[p['id'] for p in segment['plan']])
                        job.pop('candidate',None)
                    with app.db:app._save_generation(job)
            if not resumed and not audited and not args.targeted and not args.full_level:
                selected=[]; count=0
                for segment in job['segments']:
                    selected.append(segment); count+=segment['count']
                    if count>=3:break
                job['segments'] = selected
                job['count'] = count
                with app.db: app._save_generation(job)
            baseline=dict(retries=sum(s.get('retry_count',0) for s in job['segments']),
                          recoveries=job.get('recovery_count',0),global_round=job.get('global_review',{}).get('round',0))
            record = dict(request=request, count=job['count'], events=[],baseline=baseline,
                          resumed_review_ids=rereview if resumed else [])
            report['cases'].append(record)
            while len(app.trace) < args.max_calls:
                public = app.study_generation_step({'id': job['id']})
                job = app._generation(job['id'])
                record['events'].append({k: public[k] for k in
                    ('status', 'phase', 'completed_questions', 'retry_count', 'global_review', 'recovery_count')})
                record.update(status=public['status'], retry_count=public['retry_count'],
                              api_calls=len(app.trace)-start, completed_questions=public['completed_questions'])
                save(folder / 'report.json', report)
                print(args.round, request['level'], request.get('type_id', 'full'), public['phase'],
                      public['completed_questions'], '/', job['count'], 'revisions', public['retry_count'],
                      'calls', len(app.trace), flush=True)
                if public['status'] == 'complete':
                    record['paper'] = app._paper(public['paper_id'])
                    break
                if (public['retry_count']-baseline['retries'] > args.max_revisions or
                    public['recovery_count']-baseline['recoveries'] > 2 or
                    public['global_review']['round']-baseline['global_round'] > args.max_revisions + 1):
                    record['status'] = 'stopped_at_budget_or_error'
                    break
                # Do not spin while the production request recovery backoff is active.
                if public['retry_after']:time.sleep(public['retry_after'])
            if record['status'] != 'complete':
                if record['status']=='active':record['status']='stopped_at_call_budget'
                record['unfinished_job'] = job
                app.study_generation_cancel({'id': job['id']})
            save(folder / 'report.json', report)
    finally:
        report['api_calls'] = len(app.trace)
        report['finished'] = datetime.now(timezone.utc).isoformat()
        save(folder / 'report.json', report)
        app.close()
    if len(report['cases']) != len(requests) or any(c['status']!='complete' for c in report['cases']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
