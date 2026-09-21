"""Opt-in real API validation, isolated from the learner database. Requires .env."""
import json
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from service import Service

checks=[]
with tempfile.TemporaryDirectory(prefix='haru-live-') as directory:
    app=Service(directory)
    def check(name, action, params):
        started=time.monotonic()
        result=app.route(action,params)
        checks.append({'check':name,'ok':True,'seconds':round(time.monotonic()-started,2),'model':result.get('model') if isinstance(result,dict) else None})
        print(json.dumps(checks[-1],ensure_ascii=False),flush=True)
        return result
    check('API connectivity','ping',{})
    lesson=check('generated daily lesson','lesson',{'day':1,'source':'ai'})
    full=app.content(lesson['id'])
    check('lesson completion','grade',{'id':lesson['id'],'answers':[q['answer'] for q in full['questions']]})
    check('generated flashcard','card_create',{'word':'お茶'})
    check('conversation turn','chat',{'session':'cafe','message':'こんにちは。水をください。'})
    check('grammar decoder','decode',{'text':'わたしは中国人です。'})
    quiz=check('evidence-scoped weekly quiz','quiz',{'source':'ai'})
    full=app.content(quiz['id'])
    check('weekly quiz scoring','grade',{'id':quiz['id'],'answers':[q['answer'] for q in full['questions']]})
    check('immersion story','immersion',{'topic':'早上的问候'})
    check('learning archive export','export',{'type':'json'})
    # Save representative non-personal outputs for local inspection, never credentials.
    out=Path(__file__).resolve().parents[1]/'runtime/validation'
    out.mkdir(parents=True,exist_ok=True)
    (out/'live-results.json').write_text(json.dumps({'checks':checks,'sample_lesson':lesson,'sample_quiz':quiz},ensure_ascii=False,indent=2))
    app.close()
print('ALL LIVE CHECKS PASSED',flush=True)
