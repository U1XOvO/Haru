/* Duplicate-option and review retries: real loopback RPC, isolated DB, no live AI. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const data=fs.mkdtempSync(path.join(os.tmpdir(),'haru-jlpt-retry-ui-'));
const script=`import sys,os,time,json,copy
from pathlib import Path
sys.path[:0]=['backend','scripts','tests']
import service
from curriculum import CARDS
from test_study_generation import generation_fixture
from llm import AppError
from preview import Handler,ThreadingHTTPServer
control=Path(os.environ['HARU_DATA_DIR'])
review_count=0
global_review_count=0
versions={}
def generate(task,context,schema):
 global review_count,global_review_count
 if context.get('review_scope')=='global':
  time.sleep(.3)
  global_review_count+=1
  assert len(context['questions'])==5
  assert context.get('level')=='N5' and context.get('question_types')
  assert all(item.get('id') and item.get('count') for item in context['question_types'])
  if global_review_count==1:
   result=dict(approved=False,summary='离线整卷审查：q4与整卷要求不一致。',
    issues=[dict(question_ids=['q4'],reason='离线模拟：q4题干需要补充区分性语境。',material_issue=False)])
  else:
   result=dict(approved=True,summary='离线整卷复审：全部通过。',issues=[])
  with (control/'calls.jsonl').open('a') as stream:
   stream.write(json.dumps(dict(review=True,global_review=True,kind='global-changes' if global_review_count==1 else 'global-pass',
    ids=[q['id'] for q in context['questions']]))+'\\n')
  return result,'fixture-global-reviewer'
 if 'question_plan' in context and 'type_id' in context:
  time.sleep(.3)
  result,model=generation_fixture(task,context,schema)
  review='questions' in context
  kind='pass'
  if review:
   review_count+=1
   if (control/'reject-every-review').exists():
    rejected=result['reviews'][0]
    rejected.update(approved=False,issues='离线模拟：此题仍有歧义。')
    result['approved']=False
    kind='repeat-rejection'
   elif review_count==1:
    rejected=next(r for r in result['reviews'] if r['id']=='q2')
    rejected.update(approved=False,issues='离线模拟：q2的题干不足以排除干扰项。')
    result['approved']=False
    kind='ambiguous-q2'
   elif review_count==2:
    rejected=next(r for r in result['reviews'] if r['id']=='q2')
    rejected['answer']=1
    kind='answer-mismatch-q2'
  else:
   for question in result['questions']:
    identity=question['id']
    versions[identity]=versions.get(identity,0)+1
    question['prompt']+=' / 出题版本'+str(versions[identity])
    if identity=='q1' and versions[identity]==1:
     question['options'][1]=question['options'][0]
     kind='duplicate-options-q1'
  with (control/'calls.jsonl').open('a') as stream:
   stream.write(json.dumps(dict(review=review,kind=kind,
    ids=[q['id'] for q in context['question_plan']]))+'\\n')
  return result,model
 if schema.get('word') is not None:
  return copy.deepcopy(CARDS[0]),'jlpt-retry-fixture'
 raise AppError('本测试仅使用离线fixture。')
service.generate=generate
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
print(server.server_port,flush=True)
server.serve_forever()`;
const server=spawn(path.join(root,'.venv/bin/python'),['-u','-c',script],{
 cwd:root,env:{...process.env,HARU_DATA_DIR:data},stdio:['ignore','pipe','inherit']
});

(async()=>{
 let browser,page;
 const errors=[],steps=[],reads=[];
 try{
  const port=await new Promise((resolve,reject)=>{
   const timer=setTimeout(()=>reject(Error('preview timeout')),10000);
   server.stdout.once('data',chunk=>{clearTimeout(timer);resolve(Number(chunk.toString().trim()));});
   server.once('error',reject);
   server.once('exit',code=>{if(code)reject(Error('preview exited '+code));});
  });
  browser=await chromium.launch({headless:true,channel:process.env.HARU_BROWSER_CHANNEL||'chrome'});
  page=await browser.newPage({viewport:{width:1024,height:960}});
  page.on('pageerror',error=>errors.push(error.message));
  page.on('response',response=>{
   if(!response.url().endsWith('/rpc'))return;
   if(response.request().postDataJSON()?.action!=='study_generation_step')return;
   reads.push(response.json().then(body=>{if(body.ok)steps.push(body.data);}));
  });
  await page.goto(`http://127.0.0.1:${port}`);
  await page.waitForFunction(()=>!!state&&!dailyWordLoading);
  await page.locator('#nav [data-nav=jlpt]').click();
  await page.locator('#study-generate-form').waitFor();
  const start=async()=>{
   await page.locator('#study-generate-form input[name=mode][value=targeted]').check();
   await page.locator('#study-generate-form select[name=type_id]').selectOption('kanji_reading');
   await page.locator('#study-generate-form select[name=count]').selectOption('5');
   await page.locator('#study-generate-form button[type=submit]').click();
  };
  await start();
  await page.waitForFunction(()=>studyGenerationJob?.retrying&&studyGenerationJob.retry_count===1);
  const firstRetry=await page.evaluate(()=>({job:studyGenerationJob,running:studyGeneration,
   text:document.querySelector('.study-generation-retry')?.innerText||'',
   resumeButtons:document.querySelectorAll('[data-study=generation-resume]').length}));
  assert.equal(firstRetry.job.status,'active');assert.equal(firstRetry.running,true);
  assert.equal(firstRetry.job.phase,'generating');
  assert.equal(firstRetry.job.retry_question_count,1);assert.equal(firstRetry.resumeButtons,0);
  assert.ok(firstRetry.job.retry_message);assert.match(firstRetry.text,/重出|重试|重写|重新命题/);
  assert.match(firstRetry.text,/1\s*题/);assert.match(firstRetry.text,/1\s*轮/);
  const duplicateCalls=fs.readFileSync(path.join(data,'calls.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
  assert.equal(duplicateCalls[0].kind,'duplicate-options-q1');
  assert.equal(duplicateCalls.filter(call=>call.review).length,0,'a duplicate-option draft reached the reviewer before repair');
  await page.waitForFunction(()=>studyGenerationJob?.retrying&&studyGenerationJob.retry_count===2);
  const secondRetry=await page.evaluate(()=>({job:studyGenerationJob,running:studyGeneration,
   text:document.querySelector('.study-generation-retry')?.innerText||'',
   resumeButtons:document.querySelectorAll('[data-study=generation-resume]').length}));
  assert.equal(secondRetry.job.status,'active');assert.equal(secondRetry.running,true);
  assert.equal(secondRetry.job.current_retry_count,2);assert.equal(secondRetry.job.retry_question_count,1);
  assert.equal(secondRetry.resumeButtons,0);assert.match(secondRetry.text,/2\s*轮/);
  await page.waitForFunction(()=>studyGenerationJob?.retrying&&studyGenerationJob.retry_count===3);
  const thirdRetry=await page.evaluate(()=>({job:studyGenerationJob,running:studyGeneration,
   resumeButtons:document.querySelectorAll('[data-study=generation-resume]').length}));
  assert.equal(thirdRetry.job.status,'active');assert.equal(thirdRetry.running,true);
  assert.equal(thirdRetry.job.current_retry_count,3);assert.equal(thirdRetry.job.retry_question_count,1);
  assert.equal(thirdRetry.resumeButtons,0);
  await page.waitForFunction(()=>studyGenerationJob?.phase==='global_review');
  assert.equal(await page.locator('[aria-label="生成步骤"] li').count(),4);
  assert.match(await page.locator('.study-global-review').innerText(),/全局审查/);
  assert.equal(await page.evaluate(async()=>{
   const catalog=await rpc('study_catalog',{level:'N5'});
   return catalog.papers.filter(p=>p.source_type==='ai').length;
  }),0,'a paper was published before global review approved it');
  await page.waitForFunction(()=>studyGenerationJob?.global_review?.status==='changes_requested');
  const globalRevision=await page.evaluate(()=>studyGenerationJob);
  assert.equal(globalRevision.status,'active');assert.equal(globalRevision.global_revision_count,1);
  assert.equal(globalRevision.retry_question_count,1);
  assert.match(await page.locator('.study-global-review').innerText(),/自动修改/);
  assert.equal(await page.locator('[data-study=generation-resume]').count(),0);
  assert.equal(await page.evaluate(async()=>{
   const catalog=await rpc('study_catalog',{level:'N5'});
   return catalog.papers.filter(p=>p.source_type==='ai').length;
  }),0,'a paper with global-review issues was published');
  await page.waitForFunction(()=>!studyGeneration&&studyGenerationJob?.status==='complete',null,{timeout:30000});
  const complete=await page.evaluate(()=>studyGenerationJob);
  assert.equal(complete.count,5);assert.equal(complete.completed_questions,5);assert.equal(complete.retry_count,4);
  assert.equal(complete.global_revision_count,1);assert.equal(complete.global_review.status,'approved');
  assert.equal(complete.global_review.round,2);assert.equal(complete.global_review.model,'fixture-global-reviewer');
  assert.equal(await page.evaluate(()=>studyCatalog.papers.filter(p=>p.source_type==='ai').length),1);
  await Promise.all(reads);
  const completedSteps=steps.filter(step=>step.id===complete.id);
  assert.ok(completedSteps.length>=14);
  assert.ok(completedSteps.every(step=>['active','complete'].includes(step.status)),'review rejection paused the job');
  assert.ok(completedSteps.some(step=>step.phase==='global_review'));
  assert.ok(completedSteps.some(step=>step.global_review?.status==='approved'&&step.status==='active'));
  assert.ok(completedSteps.filter(step=>step.status==='complete').every(step=>step.global_review?.status==='approved'));
  const calls=fs.readFileSync(path.join(data,'calls.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
  assert.deepEqual(calls.filter(call=>!call.review).map(call=>call.ids),
   [['q1','q2','q3'],['q1'],['q2'],['q2'],['q4','q5'],['q4']],'only the duplicate-option question or questions identified by review should be regenerated');
  assert.equal(calls.filter(call=>call.kind==='duplicate-options-q1').length,1);
  assert.deepEqual(calls.filter(call=>call.review&&!call.global_review).map(call=>call.kind),
   ['ambiguous-q2','answer-mismatch-q2','pass','pass','pass']);
  assert.deepEqual(calls.filter(call=>call.review&&!call.global_review).map(call=>call.ids),
   [['q1','q2','q3'],['q2'],['q2'],['q4','q5'],['q4']],
   'valid siblings retained during duplicate repair must receive their initial independent review; already approved questions must not be reviewed again locally');
  assert.deepEqual(calls.filter(call=>call.global_review).map(call=>call.kind),['global-changes','global-pass']);
  await page.locator(`[data-study=start][data-id="${complete.paper_id}"][data-mode=practice]`).click();
  await page.waitForFunction(()=>studyAttempt?.paper.questions.length===5);
  const prompts=await page.evaluate(()=>Object.fromEntries(studyAttempt.paper.questions.map(q=>[q.id,q.prompt])));
  assert.match(prompts.q1,/出题版本2$/);assert.match(prompts.q3,/出题版本1$/);
  assert.match(prompts.q2,/出题版本3$/);assert.match(prompts.q4,/出题版本2$/);assert.match(prompts.q5,/出题版本1$/);
  assert.equal(await page.evaluate(()=>studyAttempt.paper.questions.every(q=>new Set(q.options).size===q.options.length)),true,
   'the saved paper still contains duplicate options');
  await page.locator('[data-study=back]').click();await page.locator('#study-generate-form').waitFor();

  // An indefinitely rejected fixture remains explicitly cancellable during retry.
  fs.writeFileSync(path.join(data,'reject-every-review'),'reject');
  await start();
  await page.waitForFunction(()=>studyGeneration&&studyGenerationJob?.retrying);
  const cancelledId=await page.evaluate(()=>studyGenerationJob.id);
  await page.locator('[data-study=generation-cancel]').click();
  await page.waitForFunction(()=>!studyGeneration&&studyGenerationJob?.status==='cancelled');
  const cancelled=await page.evaluate(id=>rpc('study_generation_status',{id}),cancelledId);
  assert.equal(cancelled.status,'cancelled');
  assert.equal(await page.evaluate(()=>studyCatalog.papers.filter(p=>p.source_type==='ai').length),1);
  assert.equal(await page.locator('#study-generate-form fieldset').isDisabled(),false);
  await Promise.all(reads);
  assert.deepEqual(errors,[]);
  console.log('JLPT review retry UI passed: initial duplicate options regenerate only q1 and all three first-segment questions receive independent review; local review retries only q2 twice; global review requests only q4 to change, local recheck and a second global review approve before publishing five questions without duplicate options; progress stays active and visible, accepted questions stay unchanged, cancellation works; no live AI.');
 }catch(error){
  console.error('Isolated retry fixture data: '+data);
  if(page&&!page.isClosed())console.error((await page.locator('#main').innerText()).slice(0,12000));
  throw error;
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exitCode=1;});
