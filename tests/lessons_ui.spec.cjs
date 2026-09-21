/* Daily lessons with real local RPC and isolated storage; model calls are fixtures. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const data=fs.mkdtempSync(path.join(os.tmpdir(),'haru-lessons-ui-'));
const script=`import sys,copy
sys.path[:0]=['backend','scripts']
import service
from curriculum import SEEDS,CARDS
from preview import Handler,ThreadingHTTPServer
s=service.Service()
s.lesson({'day':1,'source':'builtin'})
d=copy.deepcopy(SEEDS[1])
for k in ('design_version','objectives','sections','vocabulary','dialogue','production'):d.pop(k)
d.update(day=2,source='AI生成',model='legacy-fixture')
d['questions']=d['questions'][:3]
s.save('lesson',d);s.close()
def fixture(task,context,schema):
 if 'lesson_design' in context:
  if context['lesson_no']==3:return {'title':'invalid-fixture'},'fixture'
  return copy.deepcopy(SEEDS[context['lesson_no']-1]),'lesson-ui-fixture'
 return copy.deepcopy(CARDS[0]),'word-ui-fixture'
service.generate=fixture
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
print(server.server_port,flush=True)
server.serve_forever()`;
const server=spawn(path.join(root,'.venv/bin/python'),['-u','-c',script],{cwd:root,env:{...process.env,HARU_DATA_DIR:data},stdio:['ignore','pipe','inherit']});
(async()=>{let browser;try{
 const port=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('preview timeout')),10000);server.stdout.once('data',d=>{clearTimeout(timer);resolve(Number(d.toString().trim()));});server.once('error',reject);});
 browser=await chromium.launch({headless:true,channel:'chrome'});
 const page=await browser.newPage({viewport:{width:1360,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(`http://127.0.0.1:${port}`);
 await page.waitForFunction(()=>!!state&&!dailyWordLoading);
 let generations=0;
 page.on('request',r=>{if(r.url().endsWith('/rpc')){const d=r.postDataJSON();if(d.action==='lesson'&&d.params.source==='ai'&&!d.params.cached_only&&!d.params.id)generations++;}});
 await page.locator('.hero [data-action=start-lesson]').click();
 await page.waitForFunction(()=>selectedLessonNo===1&&!lessonLoading);
 assert.equal(await page.locator('.course-choice').count(),28);
 assert.equal(await page.locator('#lesson-day,[data-action=builtin-lesson]').count(),0);
 assert.equal(await page.locator('.lesson-body .example').count(),0);
 assert.match(await page.locator('#lesson-body').innerText(),/尚未由 AI 生成/);
 assert.equal(generations,0);
 await page.getByRole('button',{name:'AI 创建课程',exact:false}).click();
 await page.getByRole('heading',{name:'从「こんにちは」开始',exact:true}).waitFor();
 assert.equal(await page.locator('.lesson-section').count(),3);
 assert.equal(await page.locator('.lesson-vocabulary .lesson-word').count(),8);
 assert.equal(await page.locator('.lesson-body .example').count(),6);
 assert.equal(await page.locator('.lesson-roadmap li').count(),3);
 await page.getByRole('button',{name:'口语与听力',exact:true}).click();
 assert.equal(await page.locator('.dialogue-turn').count(),4);
 assert.equal(await page.locator('.production-task').count(),2);
 assert.equal(await page.locator('.production-task details[open]').count(),0);
 await page.locator('.production-task summary').first().click();
 assert.equal(await page.locator('.production-task details[open]').count(),1);
 await page.getByRole('button',{name:'课后练习',exact:true}).click();
 assert.equal(await page.locator('.quiz-question').count(),8);
 assert.equal(await page.locator('.quiz-question [data-speak]').count(),2);
 assert.equal(await page.evaluate(()=>lesson.questions.some(q=>'answer' in q||'explanation' in q)),false);
 const answers=JSON.parse(fs.readFileSync(path.join(root,'data/builtin_lessons.json'),'utf8'))[0].questions.map(q=>q.answer);
 for(let i=0;i<answers.length;i++)await page.locator(`input[name=q${i}][value="${answers[i]}"]`).check();
 await page.getByRole('button',{name:'提交答案',exact:false}).click();
 await page.locator('#quiz-result .score-banner').waitFor();
 assert.match(await page.locator('#quiz-result').innerText(),/100/);
 assert.equal(await page.evaluate(()=>state.stats.lessons),1);
 // Existing AI content is readable, and is upgraded only by an explicit action.
 await page.locator('.course-choice[data-day="2"]').click();
 await page.waitForFunction(()=>lesson?.day===2);
 assert.match(await page.locator('.lesson-body').innerText(),/已保存的简版课程/);
 const oldId=await page.evaluate(()=>lesson.id);
 await page.locator('.course-selector details summary').click();
 await page.getByRole('button',{name:'重建所选 AI 课',exact:true}).click();
 await page.waitForFunction(()=>lesson.design_version===2&&lesson.model==='lesson-ui-fixture');
 assert.notEqual(await page.evaluate(()=>lesson.id),oldId);
 assert.equal(await page.evaluate(id=>rpc('lesson',{id}).then(l=>l.questions.length),oldId),3);
 // Switching clears prior content; failed generation leaves this course empty.
 await page.locator('.course-choice[data-day="3"]').click();
 await page.waitForFunction(()=>selectedLessonNo===3&&!lessonLoading);
 assert.equal(await page.evaluate(()=>lesson),null);
 await page.getByRole('button',{name:'AI 创建课程',exact:false}).click();
 await page.waitForFunction(()=>document.querySelector('#toast').textContent.includes('两次未通过'));
 assert.equal(await page.evaluate(()=>lesson),null);
 assert.match(await page.locator('#lesson-body').innerText(),/尚未由 AI 生成/);
 assert.equal(await page.evaluate(()=>rpc('history',{kind:'lesson'}).then(ls=>ls.filter(l=>l.day===3).length)),0);
 for(let n=4;n<=7;n++){
  await page.evaluate(n=>openLesson(n,true),n);
  assert.equal(await page.locator('.lesson-section').count(),3);
  await page.getByRole('button',{name:'课后练习',exact:true}).click();
  assert.equal(await page.locator('.quiz-question').count(),8);
 }
 // Slow prior reads must not replace a more recently selected course.
 let releaseRead,enteredRead;
 const delayed=new Promise(resolve=>enteredRead=resolve);
 const barrier=new Promise(resolve=>releaseRead=resolve);
 await page.route('**/rpc',async route=>{
  const d=route.request().postDataJSON();
  if(d.action==='lesson'&&d.params.day===4&&d.params.cached_only){enteredRead();await barrier;}
  await route.continue();
 });
 const generationCount=generations;
 await page.locator('.course-choice[data-day="4"]').click();await delayed;
 await page.locator('.course-choice[data-day="5"]').click();
 await page.waitForFunction(()=>lesson?.day===5);
 releaseRead();await page.waitForFunction(()=>busyCount===0);
 assert.equal(await page.evaluate(()=>lesson.day),5);
 assert.equal(await page.locator('.course-choice[aria-pressed=true]').getAttribute('data-day'),'5');
 assert.equal(generations,generationCount);
 await page.unroute('**/rpc');
 // Background generation must not pull the user back from another course.
 await page.locator('.course-choice[data-day="6"]').click();
 await page.waitForFunction(()=>lesson?.day===6);
 const previousId=await page.evaluate(()=>lesson.id);
 let releaseGeneration,enteredGeneration;
 const started=new Promise(resolve=>enteredGeneration=resolve);
 const generationBarrier=new Promise(resolve=>releaseGeneration=resolve);
 await page.route('**/rpc',async route=>{
  const d=route.request().postDataJSON();
  if(d.action==='lesson'&&d.params.day===6&&d.params.regenerate){enteredGeneration();await generationBarrier;}
  await route.continue();
 });
 await page.locator('.course-selector details summary').click();
 await page.getByRole('button',{name:'重建所选 AI 课',exact:true}).click();await started;
 await page.locator('.course-choice[data-day="8"]').click();
 await page.waitForFunction(()=>selectedLessonNo===8&&!lessonLoading);
 releaseGeneration();await page.waitForFunction(()=>busyCount===0);
 assert.equal(await page.evaluate(()=>selectedLessonNo),8);
 assert.equal(await page.evaluate(()=>lesson),null);
 await page.unroute('**/rpc');
 await page.locator('.course-choice[data-day="6"]').click();
 await page.waitForFunction(()=>lesson?.day===6);
 assert.notEqual(await page.evaluate(()=>lesson.id),previousId);
 const countAfterRebuild=generations;
 // Beyond the seven built-in outlines also stays empty without an API request.
 await page.locator('.course-choice[data-day="8"]').click();
 await page.waitForFunction(()=>selectedLessonNo===8&&!lessonLoading);
 assert.equal(await page.evaluate(()=>lesson),null);
 assert.equal(generations,countAfterRebuild);
 await page.locator('.course-choice[data-day="7"]').click();
 await page.waitForFunction(()=>lesson?.day===7);
 const folder=path.join(root,'runtime/validation/lessons');fs.mkdirSync(folder,{recursive:true});
 await page.waitForFunction(()=>!document.querySelector('#toast').classList.contains('show'));
 for(const width of [840,1024,1360]){
  await page.setViewportSize({width,height:1000});
  for(const [label,tab] of [['语法与例句','grammar'],['口语与听力','speaking'],['课后练习','exercise']]){
   await page.getByRole('button',{name:label,exact:true}).click();
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${width} ${tab} overflow`);
   if(width===1360)await page.screenshot({path:path.join(folder,`${tab}.png`),fullPage:true});
  }
 }
 await page.evaluate(()=>{lesson.sections[0].explanation='<img src=x onerror=alert(1)>';lesson.vocabulary[0].meaning='<script>bad</script>';setLessonTab('grammar');});
 assert.equal(await page.locator('.lesson-body img,.lesson-body script').count(),0);
 assert.match(await page.locator('.lesson-section').first().innerText(),/<img/);
 await page.reload();await page.waitForFunction(()=>!!state);
 assert.equal(await page.evaluate(()=>state.stats.lessons),1);
 assert.deepEqual(errors,[]);
 console.log('Daily lesson UI passed: AI-only course list, empty states, explicit generation, stale-read protection, objectives, vocabulary, dialogue, hidden production samples, 8-question grading, legacy upgrade, failed generation preservation, escaping, 9 responsive states.');
}finally{if(browser)await browser.close();server.kill();}})().catch(e=>{console.error('Isolated database: '+data);console.error(e);process.exitCode=1;});
