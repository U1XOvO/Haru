/* Browser -> real local RPC -> isolated SQLite; all AI output is a named fixture. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const data=fs.mkdtempSync(path.join(os.tmpdir(),'haru-jlpt-generation-ui-'));
const folder=path.join(root,'runtime/validation/jlpt-layout');
fs.mkdirSync(folder,{recursive:true});
const script=`import sys,os,time,json,copy
from pathlib import Path
sys.path[:0]=['backend','scripts','tests']
import service
from curriculum import CARDS
from test_study_generation import generation_fixture
from llm import AppError
from preview import Handler,ThreadingHTTPServer
control=Path(os.environ['HARU_DATA_DIR'])
def generate(task,context,schema):
 if context.get('review_scope')=='global':
  return generation_fixture(task,context,schema)
 if 'question_plan' in context and 'type_id' in context:
  time.sleep(.15)
  failure=control/'fail-next-generation'
  if failure.exists():
   failure.unlink()
   raise AppError('离线模拟网络失败；已审定分段保留。')
  with (control/'calls.jsonl').open('a') as stream:
   stream.write(json.dumps(dict(type_id=context['type_id'],review='questions' in context,
    ids=[q['id'] for q in context['question_plan']]))+'\\n')
  return generation_fixture(task,context,schema)
 if schema.get('word') is not None:
  return copy.deepcopy(CARDS[0]),'jlpt-layout-fixture'
 raise AppError('本次界面验证仅使用离线fixture，不调用在线AI。')
service.generate=generate
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
print(server.server_port,flush=True)
server.serve_forever()`;
const server=spawn(path.join(root,'.venv/bin/python'),['-u','-c',script],{
 cwd:root,env:{...process.env,HARU_DATA_DIR:data},stdio:['ignore','pipe','inherit']
});

(async()=>{
 let browser,page;
 const errors=[],layouts=[];
 try{
  const port=await new Promise((resolve,reject)=>{
   const timer=setTimeout(()=>reject(Error('preview timeout')),10000);
   server.stdout.once('data',d=>{clearTimeout(timer);resolve(Number(d.toString().trim()));});
   server.once('error',reject);
   server.once('exit',code=>{if(code)reject(Error('preview exited '+code));});
  });
  browser=await chromium.launch({headless:true,channel:process.env.HARU_BROWSER_CHANNEL||'chrome'});
  const context=await browser.newContext({viewport:{width:1360,height:960}});
  const watch=p=>p.on('pageerror',e=>errors.push(e.message));
  page=await context.newPage();watch(page);
  const url=`http://127.0.0.1:${port}`;
  const nav=async target=>{
   await page.locator(`#nav [data-nav=${target}]`).click();
   await page.waitForFunction(target=>window.document.querySelector(`#nav [data-nav="${target}"]`)?.classList.contains('active'),target);
   await page.locator('#main').waitFor();
  };
  const job=()=>page.evaluate(()=>studyGenerationJob);
  const finish=async()=>{
   await page.waitForFunction(()=>!studyGeneration&&studyGenerationJob?.status==='complete',null,{timeout:90000});
   return job();
  };
  const countAI=()=>page.evaluate(()=>studyCatalog.papers.filter(p=>p.source_type==='ai').length);
  const target=async(type,count)=>{
   await page.locator('#study-generate-form input[name=mode][value=targeted]').check();
   await page.locator('#study-generate-form select[name=type_id]').selectOption(type);
   await page.locator('#study-generate-form select[name=count]').selectOption(String(count));
   await page.locator('#study-generate-form button[type=submit]').click();
  };
  const screenshot=async(name,width)=>{
   await page.setViewportSize({width,height:960});
   await page.evaluate(()=>document.fonts.ready);
   await page.waitForFunction(()=>!document.querySelector('#toast')?.classList.contains('show'));
   await page.evaluate(()=>window.scrollTo(0,0));
   const geometry=await page.evaluate(()=>{
    const main=document.querySelector('#main');
    return {viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,
     mainWidth:main.clientWidth,mainScrollWidth:main.scrollWidth};
   });
   assert.ok(geometry.documentWidth<=width+1,`${name}: document overflow at ${width}: ${JSON.stringify(geometry)}`);
   assert.ok(geometry.mainScrollWidth<=geometry.mainWidth+1,`${name}: main overflow at ${width}: ${JSON.stringify(geometry)}`);
   const filename=`${name}-${width}.png`;
   await page.screenshot({path:path.join(folder,filename),fullPage:true});
   layouts.push({name,width,filename,...geometry});
  };
  await page.goto(url);await page.waitForFunction(()=>!!state&&!dailyWordLoading);
  await nav('jlpt');await page.locator('#study-generate-form').waitFor();

  // The workbook-specific counts and type lists are visible for all five levels.
  for(const [level,total] of Object.entries({N1:107,N2:107,N3:102,N4:98,N5:91})){
   await page.locator(`[data-study=level][data-level=${level}]`).click();
   await page.waitForFunction(lv=>studyCatalog.level===lv,level);
   assert.equal(await page.evaluate(()=>studyCatalog.blueprint.count),total);
   assert.match(await page.locator('.study-generation-modes').innerText(),new RegExp(`${total} 题`));
   assert.equal(await page.locator('.study-blueprint li').count(),await page.evaluate(()=>studyCatalog.blueprint.types.length));
   assert.equal(await page.locator('.study-paper [data-study=delete-paper]').count(),0);
  }

  // Full N5 job: visible author/reviewer phases, progress, and navigation stays usable.
  await page.locator('#study-generate-form button[type=submit]').click();
  await page.waitForFunction(()=>studyGenerationJob?.phase==='reviewing');
  await page.getByText('审题子智能体独立复核',{exact:true}).waitFor();
  assert.equal(await page.locator('[aria-label="生成步骤"] li').count(),4);
  await page.waitForFunction(()=>studyGenerationJob?.completed_questions>=3);
  assert.equal(await countAI(),0);
  assert.ok(Number(await page.locator('progress[aria-label="生成与审查进度"]').getAttribute('value'))>0);
  await page.locator('.study-generation-segments summary').click();
  assert.equal(await page.locator('.study-generation-segments li').count(),(await job()).total_segments);
  await screenshot('jlpt-progress',840);
  const beforeNavigation=await job();
  await nav('cards');await page.locator('#card-form').waitFor();
  await page.waitForFunction(n=>studyGenerationJob.completed_questions>n,beforeNavigation.completed_questions);
  await nav('jlpt');await page.locator('#study-generation-progress').waitFor();
  const beforeClose=await job();
  assert.ok(beforeClose.completed_questions<91);

  // A new browser page recovers persisted state without silently reissuing model calls.
  await page.close();page=await context.newPage();watch(page);
  await page.goto(url);await page.waitForFunction(()=>!!state&&!dailyWordLoading);
  await nav('jlpt');await page.locator('[data-study=generation-resume]').waitFor();
  const recovered=await job();
  assert.equal(recovered.id,beforeClose.id);
  assert.ok(recovered.completed_questions>=beforeClose.completed_questions);
  assert.equal(await page.evaluate(()=>studyGeneration),false);
  await screenshot('jlpt-resume',1024);
  await page.locator('[data-study=generation-resume]').click();
  const complete=await finish();
  assert.equal(complete.count,91);assert.equal(complete.completed_questions,91);
  assert.equal(await page.locator('progress[aria-label="生成与审查进度"]').getAttribute('value'),'100');
  assert.equal(await countAI(),1);
  assert.equal(await page.locator('.study-paper [data-study=delete-paper]').count(),1);
  await screenshot('jlpt-complete',1360);

  // Ten sorting questions automatically recover from a persisted network error.
  const log=()=>fs.readFileSync(path.join(data,'calls.jsonl'),'utf8').trim().split('\n').filter(Boolean).map(JSON.parse);
  const beforeCalls=log().length;
  await target('grammar_order',10);
  await page.waitForFunction(()=>studyGenerationJob?.mode==='targeted'&&studyGenerationJob.completed_questions>=3);
  fs.writeFileSync(path.join(data,'fail-next-generation'),'fail once');
  await page.waitForFunction(()=>studyGeneration&&studyGenerationJob?.status==='active'&&studyGenerationJob?.retry_after>0);
  const recovering=await job();assert.ok(recovering.completed_questions>=3);assert.ok(recovering.completed_questions<10);
  assert.ok(recovering.recovery_count>=1);assert.ok(recovering.recovery_message);
  assert.equal(await countAI(),1);
  assert.equal(await page.locator('[data-study=generation-resume]').count(),0);
  assert.match(await page.locator('.study-generation-progress').innerText(),/恢复|重试/);
  await screenshot('jlpt-recovery',840);
  const targeted10=await finish();assert.equal(targeted10.count,10);assert.equal(await countAI(),2);
  const targetedCalls=log().slice(beforeCalls);
  assert.equal(targetedCalls.filter(c=>!c.review&&c.ids.includes('q1')).length,1,'approved first segment was generated again');
  await page.locator(`[data-study=start][data-id="${targeted10.paper_id}"][data-mode=practice]`).click();
  await page.waitForFunction(()=>studyAttempt?.paper.questions.length===10);
  assert.ok(await page.evaluate(()=>studyAttempt.paper.questions.every(q=>q.type_id==='grammar_order'&&q.prompt.includes('★'))));
  for(const width of [840,1024,1360])await screenshot('jlpt-targeted-exam',width);
  await page.locator('[data-study=back]').click();await page.locator('#study-generate-form').waitFor();

  // Cancellation during a real step cannot publish a late response as a paper.
  await target('listening_response',5);
  await page.waitForFunction(()=>studyGeneration&&studyGenerationJob?.status==='active');
  const cancelledId=(await job()).id;
  await page.locator('[data-study=generation-cancel]').click();
  await page.waitForFunction(()=>!studyGeneration&&studyGenerationJob?.status==='cancelled');
  const cancelled=await page.evaluate(id=>rpc('study_generation_status',{id}),cancelledId);
  assert.equal(cancelled.status,'cancelled');assert.equal(await countAI(),2);
  await target('listening_response',5);
  const targeted5=await finish();assert.equal(targeted5.count,5);assert.equal(await countAI(),3);
  await page.locator(`[data-study=start][data-id="${targeted5.paper_id}"][data-mode=practice]`).click();
  await page.waitForFunction(()=>studyAttempt?.paper.questions.length===5);
  assert.ok(await page.evaluate(()=>studyAttempt.paper.questions.every(q=>q.options.length===3&&q.audio_text&&q.type_id==='listening_response')));
  await page.locator('[data-study=back]').click();await page.locator('#study-generate-form').waitFor();

  // Removing a card preserves submitted answers, results, and mistake snapshots.
  await page.locator(`[data-study=start][data-id="${complete.paper_id}"][data-mode=practice]`).click();
  await page.waitForFunction(()=>studyAttempt?.paper.questions.length===91);
  for(const width of [840,1024,1360])await screenshot('jlpt-full-exam',width);
  await page.locator('input[name=study-answer]').first().check();
  await page.waitForFunction(()=>studyAttempt.revision>=1);
  await page.locator('[data-study=submit]').click();await page.locator('[data-study=confirm-submit]').click();
  await page.waitForFunction(()=>studyAttempt?.status==='submitted');
  const submitted=await page.evaluate(()=>({id:studyAttempt.id,answers:studyAttempt.answers,result:studyAttempt.result}));
  await page.locator('[data-study=back]').click();await page.locator('#study-generate-form').waitFor();
  const deleteButton=page.locator(`[data-study=delete-paper][data-id="${complete.paper_id}"]`);
  const placement=await deleteButton.evaluate(button=>{
   const b=button.getBoundingClientRect(),c=button.closest('.study-paper').getBoundingClientRect();
   return {rightGap:c.right-b.right,topGap:b.top-c.top};
  });
  assert.ok(placement.rightGap<50&&placement.topGap<50,'delete action should be in card top-right');
  await deleteButton.click();await page.getByRole('button',{name:'保留试卷',exact:true}).click();
  assert.equal(await deleteButton.count(),1);
  await deleteButton.click();await page.locator('[data-study=confirm-delete-paper]').click();
  await page.waitForFunction(id=>!studyCatalog.papers.some(p=>p.id===id),complete.paper_id);
  assert.equal(await countAI(),2);
  await page.locator('[data-study=panel][data-panel=history]').click();
  await page.locator(`[data-study=resume][data-id="${submitted.id}"]`).click();
  await page.waitForFunction(id=>studyAttempt?.id===id,submitted.id);
  assert.deepEqual(await page.evaluate(()=>studyAttempt.answers),submitted.answers);
  assert.deepEqual(await page.evaluate(()=>studyAttempt.result),submitted.result);
  assert.equal(await page.locator('.answer-palette button').count(),91);
  await page.locator('[data-study=back]').click();
  await page.locator('[data-study=panel][data-panel=mistakes]').click();
  await page.locator('.study-mistake').first().waitFor();
  assert.ok(await page.locator('.study-mistake').count()>=90);
  await page.locator('[data-study=panel][data-panel=papers]').click();

  // All nine top-level views, plus JLPT settings/import, at the supported widths.
  const views=await page.locator('#nav [data-nav]').evaluateAll(nodes=>nodes.map(n=>n.dataset.nav));
  assert.equal(views.length,9);
  for(const width of [840,1024,1360]){
   for(const view of views){
    await nav(view);
    if(view==='grammar_library')await page.locator('.grammar-detail').waitFor();
    if(view==='jlpt')await page.locator('#study-generate-form').waitFor();
    await screenshot(view,width);
   }
   await page.locator('#settings-nav').click();
   await page.locator('#profile-form').waitFor();
   await screenshot('settings',width);
   await nav('jlpt');
   await page.locator('#study-generate-form input[name=mode][value=targeted]').check();
   await page.locator('#study-generate-form select[name=type_id]').selectOption('grammar_order');
   await screenshot('jlpt-targeted-form',width);
   await page.locator('[data-study=panel][data-panel=import]').click();
   await page.locator('#study-import-form').waitFor();await screenshot('jlpt-import',width);
   await page.locator('[data-study=panel][data-panel=papers]').click();
  }
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(folder,'layout-checks.json'),JSON.stringify({fixture:true,views:[...views,'settings'],layouts,errors},null,2));
  console.log(`JLPT UI passed: 91-question full paper, visible author/reviewer progress, background navigation, reopening/resume, automatic network-error recovery, cancellation, 5/10 targeted items, three-option listening, AI deletion with retained history, five blueprints; ${layouts.length} layout screenshots, no horizontal overflow or browser errors.`);
 }catch(error){
  console.error('Isolated data retained: '+data);
  if(page&&!page.isClosed()){
   console.error((await page.locator('#main').innerText()).slice(0,18000));
   await page.screenshot({path:path.join(folder,'failure.png'),fullPage:true});
  }
  throw error;
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exitCode=1;});
