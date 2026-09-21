/* Real browser -> RPC -> SQLite. Generated questions are labeled test fixtures. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const data=fs.mkdtempSync(path.join(os.tmpdir(),'haru-study-ui-'));
const script=`import sys
sys.path[:0]=['backend','scripts','tests']
import service
from test_study import fixture
from test_study_generation import generation_fixture
from llm import AppError
from preview import Handler,ThreadingHTTPServer
def generate(task,context,schema):
 if context.get('review_scope')=='global':
  return generation_fixture(task,context,schema)
 if 'question_plan' in context and 'type_id' in context:
  return generation_fixture(task,context,schema)
 if context.get('level') in ('N5','N4','N3','N2','N1') and 'count' in context:
  return fixture(context['level'],context['count'],context['skill']),'study-ui-fixture'
 raise AppError('本次 UI 验证未调用在线 AI。')
service.generate=generate
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
print(server.server_port,flush=True)
server.serve_forever()`;
const server=spawn(path.join(root,'.venv/bin/python'),['-u','-c',script],{cwd:root,env:{...process.env,HARU_DATA_DIR:data},stdio:['ignore','pipe','inherit']});
(async()=>{
 let browser;
 try{
  const port=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('preview timeout')),10000);server.stdout.once('data',d=>{clearTimeout(timer);resolve(Number(d.toString().trim()));});server.once('error',reject);});
  browser=await chromium.launch({headless:true,channel:process.env.HARU_BROWSER_CHANNEL||'chrome'});
  const page=await browser.newPage({viewport:{width:1360,height:980}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${port}`);await page.locator('#nav [data-nav=grammar_library]').click();
  await page.locator('.grammar-row').first().waitFor();
  assert.deepEqual(await page.locator('#nav .nav-item').allTextContents(),['今日学习','每日课程','语法学习','JLPT 真题','即时学习卡','真实对话','语法解码器','沉浸日语','学习进度']);
  for(const lv of ['N5','N4','N3','N2','N1']){
   await page.locator(`[data-study=level][data-level=${lv}]`).click();
   await page.waitForFunction(lv=>grammarData.level===lv&&grammarPoint.level===lv,lv);
   assert.equal(await page.locator('.grammar-row').count(),20);
  }
  await page.locator('[data-study=level][data-level=N5]').click();
  await page.waitForFunction(()=>grammarPoint.id==='n5-001');
  await page.getByRole('button',{name:'标记已阅读',exact:true}).click();
  await page.getByRole('button',{name:'已标记阅读',exact:true}).waitFor();
  await page.getByRole('button',{name:'☆ 收藏',exact:true}).click();
  await page.getByRole('button',{name:'★ 已收藏',exact:true}).waitFor();
  await page.getByLabel('语法学习状态').selectOption('favorite');assert.equal(await page.locator('.grammar-row').count(),1);
  await page.getByLabel('语法学习状态').selectOption('all');
  await page.getByRole('button',{name:'开始 3 题练习',exact:true}).click();
  await page.locator('.exam-question').waitFor();
  const answerKey=JSON.parse(fs.readFileSync(path.join(root,'data/study/grammar.json'))).items[0].questions.map(q=>q.answer);
  for(let i=0;i<3;i++){
   const revision=await page.evaluate(()=>studyAttempt.revision);
   await page.locator('input[name=study-answer]').nth(answerKey[i]).check();
   await page.waitForFunction(r=>studyAttempt.revision>r,revision);
   if(i<2)await page.getByRole('button',{name:'下一题',exact:true}).click();
  }
  await page.getByRole('button',{name:'交卷并查看结果',exact:true}).click();
  await page.getByRole('button',{name:'确认提交',exact:true}).click();
  await page.getByRole('heading',{name:'正确率 100%',exact:true}).waitFor();
  await page.reload();await page.locator('#nav [data-nav=grammar_library]').click();
  await page.getByRole('heading',{name:'正确率 100%',exact:true}).waitFor();
  await page.getByRole('button',{name:'返回目录',exact:true}).click();
  await page.waitForFunction(()=>grammarPoint?.progress.mastered===true);
  assert.equal(await page.evaluate(()=>state.progression.stage),1);
  fs.mkdirSync(path.join(root,'runtime/validation/study'),{recursive:true});
  await page.screenshot({path:path.join(root,'runtime/validation/study/grammar.png'),fullPage:true});
  await page.locator('#nav [data-nav=jlpt]').click();await page.locator('.study-paper').first().waitFor();
  for(const lv of ['N1','N2','N3','N4','N5']){
   await page.locator(`[data-study=level][data-level=${lv}]`).click();await page.waitForFunction(lv=>studyCatalog.level===lv,lv);
   assert.equal(await page.locator('.study-paper').count(),2);
  }
  await page.locator('[data-study=start][data-id=official-2018-N5][data-mode=practice]').click();
  await page.waitForFunction(()=>studyAttempt?.paper.id==='official-2018-N5');
  assert.equal(await page.locator('.answer-palette button').count(),91);
  assert.equal(await page.getByRole('button',{name:'官方答案 ↗',exact:true}).count(),0);
  await page.locator('input[name=study-answer]').nth(3).check();await page.waitForFunction(()=>studyAttempt.revision>=1);
  await page.getByRole('button',{name:'☆ 标记待检查',exact:true}).click();
  await page.getByRole('button',{name:'★ 已标记',exact:true}).waitFor();
  await page.reload();await page.locator('#nav [data-nav=jlpt]').click();
  await page.locator('input[name=study-answer]').nth(3).waitFor();assert.equal(await page.locator('input[name=study-answer]').nth(3).isChecked(),true);
  await page.getByRole('button',{name:'交卷并查看结果',exact:true}).click();await page.getByRole('button',{name:'确认提交',exact:true}).click();
  await page.waitForFunction(()=>studyAttempt.status==='submitted');assert.equal(await page.evaluate(()=>studyAttempt.result.correct),1);
  await page.getByRole('button',{name:'官方答案 ↗',exact:true}).waitFor();
  await page.getByRole('button',{name:'返回目录',exact:true}).click();
  await page.locator('[data-study=start][data-id=official-2018-N5][data-mode=timed]').click();
  await page.waitForFunction(()=>studyAttempt?.mode==='timed');assert.equal(await page.locator('.answer-palette button').count(),35);
  await page.getByRole('button',{name:'提交当前分区',exact:true}).click();await page.getByRole('button',{name:'确认提交',exact:true}).click();
  await page.waitForFunction(()=>studyAttempt.section_index===1);assert.equal(await page.locator('.answer-palette button').count(),32);
  await page.getByRole('button',{name:'返回目录',exact:true}).click();
  await page.locator('[data-study=level][data-level=N1]').click();await page.waitForFunction(()=>studyCatalog.level==='N1');
  await page.locator('#study-generate-form input[name=mode][value=targeted]').check();
  await page.locator('#study-generate-form select[name=type_id]').selectOption('reading_short');
  await page.locator('#study-generate-form select[name=count]').selectOption('10');
  await page.getByRole('button',{name:'生成 N1 专项练习',exact:true}).click();
  await page.waitForFunction(()=>!studyGeneration&&studyCatalog.papers.some(p=>p.source_type==='ai'));
  assert.equal(await page.locator('.study-paper').count(),3);
  const aiId=await page.evaluate(()=>studyCatalog.papers.find(p=>p.source_type==='ai').id);
  await page.locator(`[data-study=start][data-id="${aiId}"][data-mode=practice]`).click();await page.waitForFunction(()=>studyAttempt?.paper.source_type==='ai');
  await page.locator('[data-study=question][data-index="2"]').click();await page.locator('.study-passage').waitFor();
  for(const width of [840,1024,1360]){
   await page.setViewportSize({width,height:760});
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow at ${width}`);
  }
  await page.screenshot({path:path.join(root,'runtime/validation/study/exam.png'),fullPage:true});
  await page.getByRole('button',{name:'返回目录',exact:true}).click();
  await page.route('**/rpc',async route=>route.request().postDataJSON().action==='study_generation_step'?route.fulfill({json:{ok:false,error:'模拟 API 失败，未保存内容'}}):route.continue());
  await page.getByRole('button',{name:'生成 N1 专项练习',exact:true}).click();await page.locator('#toast.error').waitFor();
  await page.waitForFunction(()=>!studyGeneration);assert.equal(await page.locator('.study-paper').count(),3);
  await page.unroute('**/rpc');
  await page.locator('[data-study=generation-cancel]').click();
  await page.waitForFunction(()=>studyGenerationJob?.status==='cancelled');
  await page.getByRole('button',{name:'导入试卷',exact:true}).click();
  const imported={title:'导入验证',level:'N1',source:'UI fixture 原创',sections:[{id:'r',title:'阅读',seconds:300}],questions:[{id:'q1',section:'r',skill:'reading',prompt:'<img src=x onerror=alert(1)>',passage:'明日の会議は中止です。',options:['中止','予定通り'],answer:0,explanation:'材料明确说会议取消。',grammar_ids:[]}]};
  await page.locator('#study-import-json').fill(JSON.stringify(imported));await page.getByRole('button',{name:'校验并导入',exact:true}).click();
  await page.getByRole('heading',{name:'导入验证',exact:true}).waitFor();
  await page.locator('.study-paper').filter({hasText:'导入验证'}).getByRole('button',{name:'开始练习',exact:true}).click();
  await page.locator('.study-prompt').waitFor();assert.equal(await page.locator('.study-prompt img').count(),0);
  await page.locator('#nav [data-nav=progress]').click();await page.getByRole('heading',{name:'语法与 JLPT 练习',exact:true}).waitFor();
  assert.deepEqual(errors,[]);
  console.log('Study UI passed: navigation, five levels, grammar review, 91-question official sheet, persistence, timed sections, AI fixtures, failure recovery, import escaping, responsive widths.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error('Isolated data: '+data);console.error(e);process.exitCode=1;});
