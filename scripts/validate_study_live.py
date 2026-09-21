"""Opt-in live provider check; uses project .env and an isolated database.

Creates two targeted 5-question papers by default; --full-level explicitly
selects a complete workbook-sized paper (many API calls). Never prints config
secrets. This checks actual API contracts, not teacher-reviewed difficulty.
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from service import Service
from llm import AppError


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full-level',choices=['N5','N4','N3','N2','N1'])
    parser.add_argument('--max-minutes',type=int,default=240,
                        help='每个请求的最长运行时间；到时安全停止并记录进度。')
    args=parser.parse_args()
    if not 1<=args.max_minutes<=1440:
        parser.error('--max-minutes 必须在 1 到 1440 之间。')
    root=Path(__file__).resolve().parents[1]
    report=[]
    requests=([dict(level=args.full_level,mode='full')] if args.full_level else
              [dict(level='N5',mode='targeted',type_id='kanji_reading',count=5),
               dict(level='N1',mode='targeted',type_id='grammar_order',count=5)])
    with tempfile.TemporaryDirectory(prefix='haru-study-live-') as folder:
        app=Service(folder)
        try:
            for request in requests:
                lv=request['level'];count=request.get('count')
                result={}
                try:
                    result=app.study_generation_start(request)
                    deadline=time.monotonic()+args.max_minutes*60
                    while result['status']=='active':
                        if time.monotonic()>=deadline:
                            raise AppError('达到本次验证的最长运行时间，生成任务已安全停止。')
                        delay=result.get('retry_after',0) or (1 if result.get('busy') else 0)
                        if delay:
                            time.sleep(min(delay,max(0,deadline-time.monotonic())))
                        if time.monotonic()>=deadline:
                            raise AppError('达到本次验证的最长运行时间，生成任务已安全停止。')
                        result=app.study_generation_step({'id':result['id']})
                        print(lv,result['phase'],result['completed_questions'],'/',result['count'],flush=True)
                    if result['status']!='complete':
                        raise AppError(result.get('error') or result.get('recovery_message') or '生成未完成')
                    count=result['count'];paper=app._paper(result['paper_id'])
                    attempt=app.study_start(dict(id=paper['id']))
                    assert all('answer' not in q for q in attempt['paper']['questions'])
                    graded=app.study_save(dict(id=attempt['id'],revision=0,answers={q['id']:q['answer'] for q in paper['questions']},finish=True))
                    assert graded['result']['correct']==count
                    report.append(dict(request=request,count=count,model=result['model'],status='passed',paper=paper))
                    print(lv,count,result['model'],'passed',flush=True)
                except AppError as e:
                    report.append(dict(request=request,count=count,status='failed',error=str(e),
                        recovery_allowed=result.get('resume_allowed',False),
                        failure_kind=result.get('failure_kind',''),
                        completed_questions=result.get('completed_questions',0),
                        total_questions=result.get('count',count)))
                    print(lv,'failed:',str(e),flush=True)
                    # Stop on a terminal state; never reenter model calls or
                    # mutate the persisted recovery status automatically.
                    break
        finally:app.close()
    out=root/'runtime/validation/study/live-result.json';out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    if any(r['status']!='passed' for r in report):raise SystemExit(1)
