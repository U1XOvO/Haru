/* Recoverable generation errors: isolated loopback/SQLite, fixture AI only. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const data=fs.mkdtempSync(path.join(os.tmpdir(),'haru-jlpt-all-errors-ui-'));
const script=`import sys,os,time,json,copy
from pathlib import Path
sys.path[:0]=['backend','scripts','tests']
import service
from curriculum import CARDS
from test_study_generation import generation_fixture
from llm import AppError
from preview import Handler,ThreadingHTTPServer
control=Path(os.environ['HARU_DATA_DIR'])
faults=set()
versions={}
def record(kind,context):
 ids=[q['id'] for q in context.get('questions',context.get('question_plan',[]))]
 with (control/'calls.jsonl').open('a') as stream:
  stream.write(json.dumps(dict(kind=kind,ids=ids,at=time.time()))+'\\n')
def generate(task,context,schema):
 if context.get('review_scope')=='global' or 'question_plan' in context:
  time.sleep(.08)
  if (control/'force-api-failure').exists():
   record('forced-api-error',context)
   raise AppError('离线模拟：服务暂时不可用。')
  if context.get('review_scope')=='global':
   if 'global-api' not in faults:
    faults.add('global-api');record('global-api-error',context)
    raise AppError('离线模拟：全局审查连接临时失败。')
   record('global-pass',context)
   return generation_fixture(task,context,schema)
  if 'questions' in context:
   if 'review-format' not in faults:
    faults.add('review-format');record('review-format-error',context)
    return dict(approved=True,reviews='invalid-review-format'),'fixture-invalid-reviewer'
   record('local-pass',context)
   return generation_fixture(task,context,schema)
  ids=[p['id'] for p in context['question_plan']]
  if 'q4' in ids and 'author-api' not in faults:
   faults.add('author-api');record('author-api-error',context)
   raise AppError('离线模拟：命题请求临时超时。')
  result,model=generation_fixture(task,context,schema)
  for question in result['questions']:
   identity=question['id'];versions[identity]=versions.get(identity,0)+1
   question['prompt']+=' / 离线版本'+str(versions[identity])
  if 'option-count' not in faults:
   faults.add('option-count')
   result['questions'][0]['options'].pop()
   record('option-count-error',context)
  else: record('author-pass',context)
  return result,model
 if schema.get('word') is not None:
  return copy.deepcopy(CARDS[0]),'all-errors-fixture'
 raise AppError('本测试仅使用离线fixture。')
service.generate=generate
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
print(server.server_port,flush=True)
server.serve_forever()`;
const server=spawn(path.join(root,'.venv/bin/python'),['-u','-c',script],{
 cwd:root,env:{...process.env,HARU_DATA_DIR:data},stdio:['ignore','pipe','inherit']
});
const calls=()=>fs.existsSync(path.join(data,'calls.jsonl'))?
 fs.readFileSync(path.join(data,'calls.jsonl'),'utf8').trim().split('\n').filter(Boolean).map(JSON.parse):[];

(async()=>{
 let browser,page;
 const errors=[],steps=[],reads=[];
 let transportFailures=0;
 try{
  const port=await new Promise((resolve,reject)=>{
   const timer=setTimeout(()=>reject(Error('preview timeout')),10000);
   server.stdout.once('data',chunk=>{clearTimeout(timer);resolve(Number(chunk.toString().trim()));});
   server.once('error',error=>{clearTimeout(timer);reject(error);});
   server.once('exit',code=>{if(code){clearTimeout(timer);reject(Error('preview exited '+code));}});
  });
  browser=await chromium.launch({headless:true,channel:process.env.HARU_BROWSER_CHANNEL||'chrome'});
  page=await browser.newPage({viewport:{width:1024,height:960}});
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/rpc',async route=>{
   if(route.request().postDataJSON()?.action==='study_generation_step'&&transportFailures===0){
    transportFailures++;await route.abort('failed');return;
   }
   await route.continue();
  });
  page.on('response',response=>{
   if(!response.url().endsWith('/rpc')||response.request().postDataJSON()?.action!=='study_generation_step')return;
   reads.push(response.json().then(body=>{if(body.ok)steps.push(body.data);}));
  });
  await page.goto(`http://127.0.0.1:${port}`);
  await page.waitForFunction(()=>!!state&&!dailyWordLoading);
  await page.locator('#nav [data-nav=jlpt]').click();
  await page.locator('#study-generate-form').waitFor();
  await page.evaluate(()=>{
   window.__generationObservations=[];
   window.__generationObservationTimer=setInterval(()=>{
    if(!studyGenerationJob||studyGenerationJob.status==='cancelled')return;
    window.__generationObservations.push({id:studyGenerationJob.id,status:studyGenerationJob.status,
     running:studyGeneration,resume:document.querySelectorAll('[data-study=generation-resume]').length,
     text:document.querySelector('.study-generation-progress')?.innerText||''});
   },50);
  });
  const start=async()=>{
   await page.locator('#study-generate-form input[name=mode][value=targeted]').check();
   await page.locator('#study-generate-form select[name=type_id]').selectOption('kanji_reading');
   await page.locator('#study-generate-form select[name=count]').selectOption('5');
   await page.locator('#study-generate-form button[type=submit]').click();
  };
  await start();
  await page.waitForFunction(()=>studyGeneration&&studyGenerationJob?.retry_after>0,null,{timeout:30000});
  const waiting=await page.evaluate(()=>({id:studyGenerationJob.id,status:studyGenerationJob.status,
   recovery:studyGenerationJob.recovery_count,message:studyGenerationJob.recovery_message,
   resume:document.querySelectorAll('[data-study=generation-resume]').length}));
  assert.equal(waiting.status,'active');assert.equal(waiting.resume,0);
  assert.ok(waiting.recovery>=1);assert.ok(waiting.message);
  const countBeforeEarlyStep=calls().length;
  const early=await page.evaluate(id=>rpc('study_generation_step',{id}),waiting.id);
  assert.equal(early.status,'active');assert.ok(early.retry_after>0);
  assert.equal(calls().length,countBeforeEarlyStep,'a model call ignored the server backoff deadline');
  await page.waitForFunction(()=>studyGenerationJob?.phase==='global_review'&&studyGenerationJob?.retry_after>0,null,{timeout:30000});
  assert.equal(await page.locator('[data-study=generation-resume]').count(),0);
  assert.equal(await page.evaluate(async()=>{
   const catalog=await rpc('study_catalog',{level:'N5'});
   return catalog.papers.filter(p=>p.source_type==='ai').length;
  }),0,'global API failure published an unapproved paper');
  await page.waitForFunction(()=>!studyGeneration&&studyGenerationJob?.status==='complete',null,{timeout:45000});
  const complete=await page.evaluate(()=>studyGenerationJob);
  assert.equal(transportFailures,1);
  assert.equal(complete.count,5);assert.equal(complete.completed_questions,5);
  assert.equal(complete.global_review.status,'approved');assert.ok(complete.recovery_count>=2);
  assert.equal(await page.locator('[data-study=generation-resume]').count(),0);
  await Promise.all(reads);
  const jobSteps=steps.filter(step=>step.id===complete.id);
  assert.ok(jobSteps.every(step=>['active','complete'].includes(step.status)),'a recoverable error paused the job');
  assert.ok(jobSteps.some(step=>step.phase==='global_review'&&step.retry_after>0),'global API failure was not exposed as automatic recovery');
  const observations=await page.evaluate(()=>window.__generationObservations);
  assert.ok(observations.filter(o=>o.id===complete.id&&o.status==='active').every(o=>o.running&&!o.resume&&!o.text.includes('生成已暂停')),
   'the UI stopped generation or exposed a resume button during automatic recovery');
  const recorded=calls();
  for(const kind of ['option-count-error','review-format-error','author-api-error','global-api-error']){
   assert.equal(recorded.filter(call=>call.kind===kind).length,1,kind+' was not exercised exactly once');
  }
  assert.equal(recorded.filter(call=>call.kind==='global-pass').length,1);
  assert.deepEqual([...new Set(recorded.filter(call=>call.kind==='local-pass').flatMap(call=>call.ids))].sort(),['q1','q2','q3','q4','q5'],
   'some saved questions bypassed independent review after recovery');
  await page.locator(`[data-study=start][data-id="${complete.paper_id}"][data-mode=practice]`).click();
  await page.waitForFunction(()=>studyAttempt?.paper.questions.length===5);
  const paper=await page.evaluate(()=>studyAttempt.paper);
  assert.equal(new Set(paper.questions.map(q=>q.id)).size,5);
  assert.ok(paper.questions.every(q=>q.options.length===4&&new Set(q.options).size===4));
  assert.equal(paper.generation.global_review.approved,true);
  assert.deepEqual(paper.generation.segments.flatMap(s=>Object.keys(s.question_review_models)).sort(),['q1','q2','q3','q4','q5']);
  await page.locator('[data-study=back]').click();
  await page.locator('#study-generate-form').waitFor();

  // Cancellation must interrupt the recovery wait, including its pending timer.
  fs.writeFileSync(path.join(data,'force-api-failure'),'offline failure');
  await start();
  await page.waitForFunction(previous=>studyGeneration&&studyGenerationJob?.id!==previous&&studyGenerationJob?.retry_after>0,complete.id);
  const cancelId=await page.evaluate(()=>studyGenerationJob.id);
  assert.equal(await page.locator('[data-study=generation-resume]').count(),0);
  const callsAtCancellation=calls().length;
  await page.locator('[data-study=generation-cancel]').click();
  await page.waitForFunction(()=>!studyGeneration&&studyGenerationJob?.status==='cancelled',null,{timeout:2000});
  await page.waitForTimeout(2200);
  assert.equal(calls().length,callsAtCancellation,'a cancelled recovery timer made another model call');
  assert.equal((await page.evaluate(id=>rpc('study_generation_status',{id}),cancelId)).status,'cancelled');
  assert.equal(await page.evaluate(()=>studyCatalog.papers.filter(p=>p.source_type==='ai').length),1);
  assert.equal(await page.locator('#study-generate-form fieldset').isDisabled(),false);
  await page.evaluate(()=>clearInterval(window.__generationObservationTimer));
  await Promise.all(reads);assert.deepEqual(errors,[]);
  console.log('JLPT all-errors UI passed: option-count and reviewer-format errors, author/global API failures and one aborted step RPC automatically recover to a fully reviewed five-question paper; backoff suppresses early model calls, generation never pauses, and cancellation stops a pending recovery timer; no live AI.');
 }catch(error){
  console.error('Isolated all-errors fixture data: '+data);
  if(page&&!page.isClosed())console.error((await page.locator('#main').innerText()).slice(0,12000));
  throw error;
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exitCode=1;});
