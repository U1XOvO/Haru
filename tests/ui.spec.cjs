/* Opt-in integration test against scripts/preview.py using an isolated HARU_DATA_DIR. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.HARU_BROWSER_CHANNEL||'chrome'});
 const page=await browser.newPage({viewport:{width:1360,height:1000},deviceScaleFactor:1});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8765');
 await page.getByRole('heading',{name:'你好，学习者 ☀'}).waitFor();
 await page.screenshot({path:'docs/home.png',fullPage:true});
 await page.locator('.hero').getByRole('button',{name:'继续第 1 课'}).click();
 await page.getByText('这节课尚未由 AI 生成。',{exact:false}).waitFor();
 await page.getByRole('button',{name:'AI 创建课程',exact:false}).click();
 await page.getByRole('heading',{name:'从「こんにちは」开始',exact:true}).waitFor();
 await page.screenshot({path:'docs/lesson.png',fullPage:true});
 await page.getByRole('button',{name:'口语与听力',exact:true}).click();
 await page.getByRole('button',{name:'录下我的跟读'}).waitFor();
 await page.getByRole('button',{name:'课后练习',exact:true}).click();
 const answers=JSON.parse(fs.readFileSync(path.join(__dirname,'../data/builtin_lessons.json'),'utf8'))[0].questions.map(q=>q.answer);
 for(let i=0;i<answers.length;i++)await page.locator(`input[name=q${i}][value="${answers[i]}"]`).check();
 await page.getByRole('button',{name:'提交答案'}).click();
 await page.locator('#quiz-result').getByText('100').waitFor();
 await page.getByRole('button',{name:'即时学习卡',exact:true}).click();
 await page.getByRole('button',{name:'加入五张入门卡'}).click();
 await page.locator('.flashcard').waitFor();await page.locator('.flashcard').click();
 await page.getByRole('button',{name:'记住了',exact:false}).click();
 await page.waitForFunction(()=>document.querySelector('.card-title')?.textContent.includes('4 张到期'));
 await page.getByRole('button',{name:'学习进度',exact:true}).click();
 await page.getByRole('button',{name:'从已学课程抽题'}).click();
 await page.locator('#quiz-form').waitFor();
 for(const name of ['真实对话','语法解码器','沉浸日语','偏好设置']){
   await page.getByRole('button',{name,exact:true}).click();
   await page.waitForTimeout(150);
   assert.equal(await page.locator('h1').count(),1,name);
 }
 await page.locator('input[name="name"]').fill('小春');
 await page.getByRole('button',{name:'保存偏好'}).click();
 await page.waitForFunction(()=>document.querySelector('#profile-name').textContent==='小春');
 await page.reload();
 await page.getByRole('heading',{name:'你好，小春 ☀'}).waitFor();
 for(const width of [880,1024,1360]){
  await page.setViewportSize({width,height:900});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),`overflow at ${width}`);
 }
 assert.deepEqual(errors,[]);
 await browser.close();console.log('UI integration passed: navigation, lesson, scoring, cards, review, weekly test, preferences, persistence, responsive widths.');
})().catch(e=>{console.error(e);process.exit(1)});
