/* Isolated navigation race and recoverable-generation UI regression. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const data=fs.mkdtempSync(path.join(os.tmpdir(),'haru-study-navigation-'));
const script=`import sys
sys.path[:0]=['backend','scripts']
import service
from llm import AppError
from preview import Handler,ThreadingHTTPServer
def no_ai(*args,**kwargs):
 raise AppError('本次 UI 验证不调用在线 AI。')
service.generate=no_ai
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
print(server.server_port,flush=True)
server.serve_forever()`;
const server=spawn(path.join(root,'.venv/bin/python'),['-u','-c',script],{cwd:root,env:{...process.env,HARU_DATA_DIR:data},stdio:['ignore','pipe','inherit']});

(async()=>{let browser;try{
 const port=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('preview timeout')),10000);server.stdout.once('data',d=>{clearTimeout(timer);resolve(Number(d.toString().trim()));});server.once('error',reject);});
 browser=await chromium.launch({headless:true,channel:process.env.HARU_BROWSER_CHANNEL||'chrome'});
 const page=await browser.newPage({viewport:{width:1360,height:980}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 let holdGrammar=false,holdStudy=false,holdDetail=false,releaseGrammar,releaseStudy,releaseDetail,enteredGrammar,enteredStudy,enteredDetail;
 const grammarBarrier=new Promise(resolve=>releaseGrammar=resolve),studyBarrier=new Promise(resolve=>releaseStudy=resolve);
 const grammarEntered=new Promise(resolve=>enteredGrammar=resolve),studyEntered=new Promise(resolve=>enteredStudy=resolve);
 const detailBarrier=new Promise(resolve=>releaseDetail=resolve),detailEntered=new Promise(resolve=>enteredDetail=resolve);
 let injectFailed=false;const stepCalls=[];
 const failedJob={id:'fixture-failed-job',level:'N3',mode:'compat',status:'failed',phase:'reviewing',count:5,completed_questions:0,completed_segments:0,total_segments:1,current_segment:1,current_title:'语法',segments:[{title:'语法',count:5,status:'drafted',retry_count:0}],retry_count:0,recovery_count:3,recovery_kind:'provider',resume_allowed:true,error:'模型鉴权失败，api_key=sk-supersecretfixturevalue',recovery_message:'已停止自动重试。',global_review:{status:'pending',round:0,issue_count:0,model:''}};
 await page.route('**/rpc',async route=>{
  const request=route.request().postDataJSON();
  if(request.action==='grammar_catalog'&&request.params.level==='N4'&&holdGrammar){
   holdGrammar=false;const response=await route.fetch();enteredGrammar();await grammarBarrier;await route.fulfill({status:response.status(),contentType:'application/json',body:JSON.stringify(await response.json())});return;
  }
  if(request.action==='study_catalog'&&request.params.level==='N4'&&holdStudy){
   holdStudy=false;const response=await route.fetch();enteredStudy();await studyBarrier;await route.fulfill({status:response.status(),contentType:'application/json',body:JSON.stringify(await response.json())});return;
  }
  if(request.action==='grammar_detail'&&request.params.id==='n3-002'&&holdDetail){
   holdDetail=false;const response=await route.fetch();enteredDetail();await detailBarrier;await route.fulfill({status:response.status(),contentType:'application/json',body:JSON.stringify(await response.json())});return;
  }
  if(request.action==='study_catalog'&&injectFailed){
   const response=await route.fetch(),payload=await response.json();payload.data.generation={...failedJob};
   await route.fulfill({status:response.status(),contentType:'application/json',body:JSON.stringify(payload)});return;
  }
  if(request.action==='study_generation_step'){
   stepCalls.push(request.params);await route.fulfill({json:{ok:true,data:{...failedJob,resume_allowed:false,error:'连续鉴权失败，已停止自动恢复。'}}});return;
  }
  if(request.action==='study_generation_cancel'){
   await route.fulfill({json:{ok:true,data:{...failedJob,status:'cancelled',resume_allowed:false}}});return;
  }
  await route.continue();
 });
 await page.goto(`http://127.0.0.1:${port}`);
 await page.locator('#nav [data-nav=grammar_library]').click();await page.locator('.grammar-row').first().waitFor();
 holdGrammar=true;
 await page.locator('[data-study=level][data-kind=grammar][data-level=N4]').click();await grammarEntered;
 await page.locator('[data-study=level][data-kind=grammar][data-level=N3]').click();
 await page.waitForFunction(()=>grammarData?.level==='N3'&&grammarPoint?.level==='N3');
 releaseGrammar();await page.waitForFunction(()=>busyCount===0);
 assert.equal(await page.evaluate(()=>grammarData.level),'N3');assert.equal(await page.evaluate(()=>grammarPoint.level),'N3');
 assert.equal(await page.locator('.grammar-row.selected').getAttribute('data-id'),'n3-001');
 const ids=await page.locator('.grammar-row').evaluateAll(rows=>rows.map(row=>row.dataset.id));holdDetail=true;
 await page.locator(`.grammar-row[data-id="${ids[1]}"]`).click();await detailEntered;
 await page.locator(`.grammar-row[data-id="${ids[2]}"]`).click();await page.waitForFunction(id=>grammarPoint?.id===id,ids[2]);
 releaseDetail();await page.waitForFunction(()=>busyCount===0);
 assert.equal(await page.evaluate(()=>grammarPoint.id),ids[2]);assert.equal(await page.evaluate(()=>grammarSelectedPointId),ids[2]);
 assert.equal(await page.locator('.grammar-row.selected').getAttribute('data-id'),ids[2]);
 await page.locator('#nav [data-nav=jlpt]').click();await page.locator('.study-paper').first().waitFor();
 holdStudy=true;
 await page.locator('[data-study=level][data-kind=exam][data-level=N4]').click();await studyEntered;
 await page.locator('[data-study=level][data-kind=exam][data-level=N3]').click();
 await page.waitForFunction(()=>studyCatalog?.level==='N3');
 releaseStudy();await page.waitForFunction(()=>busyCount===0);
 assert.equal(await page.evaluate(()=>studyCatalog.level),'N3');
 assert.equal(await page.locator('[data-study=level][data-kind=exam].active').getAttribute('data-level'),'N3');
 injectFailed=true;await page.evaluate(()=>loadStudy('jlpt'));
 await page.waitForFunction(()=>studyGenerationJob?.status==='failed');
 await page.locator('.study-generation-failure').waitFor();
 assert.match(await page.locator('.study-generation-status .pill').innerText(),/兼容练习/);
 assert.match(await page.locator('.study-generation-failure').innerText(),/鉴权失败/);
 assert.doesNotMatch(await page.locator('.study-generation-failure').innerText(),/supersecretfixturevalue/);
 await page.waitForTimeout(100);assert.equal(stepCalls.length,0,'failed jobs must remain stopped until the user continues');
 await page.getByRole('button',{name:'修正配置后继续',exact:true}).click();
 await page.waitForFunction(()=>studyGeneration===false&&studyGenerationJob?.status==='failed'&&studyGenerationJob.resume_allowed===false);
 assert.equal(stepCalls.length,1);
 assert.deepEqual(stepCalls,[{id:'fixture-failed-job',resume:true}]);
 assert.equal(await page.locator('[data-study=generation-resume]').count(),0,'non-resumable failed jobs must not show Continue');
 await page.locator('[data-study=generation-cancel]').click();
 await page.waitForFunction(()=>studyGenerationJob?.status==='cancelled');
 assert.deepEqual(errors,[]);
 console.log('Study navigation UI passed: stale level/detail reads, selected grammar state, failed-job pause, explicit idempotent resume, and non-resumable cancel flow.');
}finally{if(browser)await browser.close();server.kill();}})().catch(e=>{console.error('Isolated fixture DB: '+data);console.error(e);process.exitCode=1;});
