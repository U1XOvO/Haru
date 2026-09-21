/* Isolated end-to-end stage transition. Requires Playwright and local Chrome. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawn,execFileSync}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const data=fs.mkdtempSync(path.join(os.tmpdir(),'haru-stage-ui-'));
const py=path.join(root,'.venv/bin/python');
const env={...process.env,HARU_DATA_DIR:data,LLM_BASE_URL:'http://offline.invalid'};
const python=code=>execFileSync(py,['-c',code],{cwd:root,env,encoding:'utf8'}).trim();
python(`import sys,copy
sys.path.insert(0,'backend')
from service import Service
from curriculum import SEEDS
from progression import course_spec
s=Service()
for n in range(1,30):
 d=copy.deepcopy(SEEDS[(n-1)%7]);d.update(day=n,lesson_no=n,stage=course_spec(n)['stage'],title=f'测试课{n}',source='AI生成',model='ui-fixture')
 for i,q in enumerate(d['questions']):q['prompt']=f'{n}-{i} '+q['prompt']
 d=s.save('lesson',d)
 if n<=28:s.grade({'id':d['id'],'answers':[q['answer'] for q in d['questions']]})
s.close()`);
const port=18765;
const server=spawn(py,['scripts/preview.py','--port',String(port)],{cwd:root,env,stdio:['ignore','pipe','pipe']});
(async()=>{
 let browser;
 try{
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('preview timeout')),10000);server.stdout.once('data',()=>{clearTimeout(timer);resolve();});server.once('error',reject);});
  browser=await chromium.launch({headless:true,channel:'chrome'});
  const page=await browser.newPage({viewport:{width:1360,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${port}`);
  await page.getByRole('button',{name:'开始阶段评估',exact:true}).click();
  await page.locator('#quiz-form').waitFor();
  const first=await page.locator('#quiz-form').getAttribute('data-id');
  async function answer(wrong=false){
   const id=await page.locator('#quiz-form').getAttribute('data-id');assert.match(id,/^[a-f0-9]+$/);
   const answers=JSON.parse(python(`import sys,json\nsys.path.insert(0,'backend')\nfrom service import Service\ns=Service();d=s.content('${id}');print(json.dumps([(q['answer']+${wrong?1:0})%len(q['options']) for q in d['questions']]));s.close()`));
   for(let i=0;i<answers.length;i++)await page.locator(`input[name=q${i}][value="${answers[i]}"]`).check();
   await page.getByRole('button',{name:'提交答案'}).click();
   await page.locator('#quiz-result .score-banner').waitFor();
  }
  await answer(true);await page.getByRole('heading',{name:'先补强，再重新评估',exact:true}).waitFor();
  await page.reload();await page.getByRole('button',{name:'打开错题补强',exact:true}).click();
  await page.getByRole('button',{name:'课后练习',exact:true}).click();await answer();
  await page.getByRole('heading',{name:'补强完成，可以重新评估',exact:true}).waitFor();
  await page.locator('#quiz-result').getByRole('button',{name:'继续学习'}).click();
  await page.waitForFunction(first=>checkpoint?.kind==='stage_assessment'&&checkpoint.id!==first&&document.querySelector('#quiz-form')?.dataset.id===checkpoint.id,first);assert.notEqual(await page.locator('#quiz-form').getAttribute('data-id'),first);
  await answer();await page.getByRole('heading',{name:'阶段通过，下一阶段已解锁',exact:true}).waitFor();
  await page.locator('#quiz-result').getByRole('button',{name:'继续学习'}).click();
  await page.getByRole('heading',{name:'测试课29',exact:true}).waitFor();
  assert.equal(await page.locator('#lesson-stage').inputValue(),'2');
  assert.equal(await page.locator('.course-choice').count(),14);
  await page.locator('#lesson-stage').selectOption('1');await page.waitForFunction(()=>document.querySelectorAll('.course-choice').length===28);
  await page.reload();await page.waitForFunction(()=>state?.progression.stage===2);
  assert.equal(await page.evaluate(()=>state.stats.lessons),28);
  for(const width of [840,1024,1360]){
   await page.setViewportSize({width,height:900});
   for(const view of ['home','lessons','progress']){
    await page.evaluate(view=>navigate(view),view);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${view} overflow at ${width}`);
   }
  }
  assert.deepEqual(errors,[]);
  console.log('Stage UI passed: 28 → failed assessment → persisted remedial → new assessment → unlocked 29; old stage review, 9 responsive views, no JS errors.');
 }catch(e){console.error('Isolated test data retained at '+data);if(browser){const p=browser.contexts()[0]?.pages()[0];if(p){console.error(await p.locator('#main').innerText());await p.screenshot({path:'runtime/validation/progression/ui-failure.png',fullPage:true});}}throw e;}finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
