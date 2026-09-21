/* Isolated browser integration. Models are explicit fixtures; no real API calls. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..'),data=fs.mkdtempSync(path.join(os.tmpdir(),'haru-learning-ui-'));
const script=`import sys,time,copy
sys.path[:0]=['backend','scripts']
import service,conversation
from curriculum import CARDS,SEEDS
from preview import Handler,ThreadingHTTPServer
from llm import AppError
def fixture(task,context,schema):
 if 'lesson_design' in context:return copy.deepcopy(SEEDS[3]),'ui-fixture'
 if 'tokens' in schema:
  sentence=context['sentence']
  tokens=[dict(surface='水',reading='みず',lemma='水',meaning='水'),dict(surface=sentence[1:],reading='',lemma='',meaning='')]
  return dict(tokens=tokens),'ui-fixture'
 if 'goals' in schema:
  mid=next(m['id'] for m in context['messages'] if m['role']=='user')
  return dict(summary='完成点水；继续练习数量和价格。',goals=[dict(met=True,message_id=mid,quote='水をください。',note='已表达请求'),dict(met=False,message_id=0,quote='',note='可以说一杯'),dict(met=False,message_id=0,quote='',note='试问价格')]),'ui-fixture'
 return copy.deepcopy(CARDS[2]),'ui-fixture'
def stream(task,context,schema,emit,cancelled):
 emit(dict(type='delta',text='はい'))
 for i in range(80 if context['message']=='停止テスト' else 8):
  if cancelled():raise AppError('已停止生成，本轮未保存。')
  time.sleep(.05)
 emit(dict(type='delta',text='はい、水です。'))
 return dict(jp='はい、水です。',kana='はい、みずです。',romaji='Hai, mizu desu.',zh='好的，是水。',feedback='请求很清楚。',suggestion='ありがとうございます。',pending_task='请说明需要几杯。'),'ui-stream-fixture'
service.generate=fixture
conversation.generate_stream=stream
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
print(server.server_port,flush=True)
server.serve_forever()`;
const server=spawn(path.join(root,'.venv/bin/python'),['-u','-c',script],{cwd:root,env:{...process.env,HARU_DATA_DIR:data},stdio:['ignore','pipe','inherit']});
(async()=>{let browser;try{
 const port=await new Promise((resolve,reject)=>{const t=setTimeout(()=>reject(new Error('server timeout')),10000);server.stdout.once('data',d=>{clearTimeout(t);resolve(Number(d.toString().trim()));});server.once('exit',code=>{if(code)reject(new Error('server exited '+code));});});
 browser=await chromium.launch({headless:true,channel:'chrome'});
 const page=await browser.newPage({viewport:{width:1360,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(`http://127.0.0.1:${port}`);await page.waitForFunction(()=>!!state&&!dailyWordLoading);
 assert.equal(await page.locator('#nav [data-nav]').count(),9);
 assert.equal(await page.getByRole('button',{name:/学习计划|学习资料|安排计划/}).count(),0);
 assert.equal(await page.locator('.home-grid > .right-rail').count(),1);
 assert.equal(await page.evaluate(()=>('plan' in state)||('resources' in state)),false);
 await page.getByRole('button',{name:'偏好设置',exact:true}).click();
 assert.equal(await page.locator('input[name="time"]').count(),0);
 await page.locator('input[name="name"]').fill('小春');
 await page.getByRole('button',{name:'保存偏好'}).click();
 await page.waitForFunction(()=>state.profile.name==='小春');
 await page.getByRole('button',{name:'导出学习档案',exact:false}).click();
 await page.getByRole('heading',{name:'已经导出学习档案',exact:true}).waitFor();
 assert.match(await page.evaluate(()=>lastExport.filename),/\.json$/);
 await page.getByRole('button',{name:'完成',exact:true}).click();
 await page.locator('#nav [data-nav="home"]').click();
 const homeFolder=path.join(root,'runtime/validation/learning');fs.mkdirSync(homeFolder,{recursive:true});
 await page.screenshot({path:path.join(homeFolder,'home.png'),fullPage:true});
 await page.evaluate(async()=>{await openLesson(4,true);});
 await page.getByRole('button',{name:'逐词注音：水をください。',exact:true}).click();
 await page.locator('.reading-word ruby rt').filter({hasText:'みず'}).waitFor();
 await page.locator('[data-lookup="水"]').first().click();
 await page.getByRole('heading',{name:'水',exact:true}).waitFor();
 await page.getByRole('button',{name:'加入词卡',exact:true}).click();
 await page.getByRole('button',{name:'已加入词卡',exact:true}).waitFor();
 await page.getByRole('button',{name:'关闭',exact:true}).click();
 const before=await page.evaluate(()=>rpc('cards'));
 await page.locator('#nav [data-nav="progress"]').click();
 await page.locator('.knowledge-panel').waitFor();
 assert.match(await page.locator('.knowledge-panel tbody').innerText(),/水/);
 assert.deepEqual(await page.evaluate(()=>rpc('cards')),before);
 await page.locator('#nav [data-nav="chat"]').click();
 await page.getByRole('button',{name:'开始场景任务',exact:true}).click();
 await page.getByRole('button',{name:'结束并点评',exact:true}).waitFor();
 await page.locator('#chat-input').fill('水をください。');
 await page.getByRole('button',{name:'发送',exact:false}).click();
 await page.locator('#stream-text').filter({hasText:'はい'}).waitFor();
 await page.waitForFunction(()=>!chatFlight&&chatMessages.length===2);
 assert.equal(await page.getByRole('button',{name:'停止生成',exact:true}).count(),0);
 await page.getByText('继续上次的问题',{exact:true}).click();
 await page.getByText('请说明需要几杯。',{exact:true}).waitFor();
 await page.locator('#chat-input').fill('停止テスト');
 await page.getByRole('button',{name:'发送',exact:false}).click();
 await page.locator('#stream-text').filter({hasText:'はい'}).waitFor();
 await page.getByRole('button',{name:'停止生成',exact:true}).click();
 await page.waitForFunction(()=>!chatFlight);
 assert.equal(await page.locator('#chat-input').inputValue(),'停止テスト');
 assert.equal(await page.evaluate(()=>chatMessages.length),2);
 await page.getByRole('button',{name:'结束并点评',exact:true}).click();
 await page.getByText('本轮任务点评',{exact:true}).waitFor();
 assert.equal(await page.locator('.task-result blockquote').innerText(),'水をください。');
 await page.getByRole('button',{name:'再练一次',exact:true}).click();
 await page.waitForFunction(()=>chatState?.status==='active'&&chatMessages.length===0);
 await page.getByText('此前任务记忆（我的原话）',{exact:true}).waitFor();
 await page.reload();await page.locator('#nav [data-nav="chat"]').click();
 await page.getByText('本轮目标',{exact:true}).waitFor();
 for(const width of [840,1024,1360]){
  await page.setViewportSize({width,height:1000});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow at ${width}`);
 }
 const folder=path.join(root,'runtime/validation/learning');fs.mkdirSync(folder,{recursive:true});
 await page.screenshot({path:path.join(folder,'task.png'),fullPage:true});
 await page.evaluate(async()=>{await openLesson(4,true);});
 await page.locator('.reading-word ruby').waitFor();
 await page.screenshot({path:path.join(folder,'reading.png'),fullPage:true});
 assert.deepEqual(errors,[]);
 console.log('Learning UI passed: cached ruby, contextual lookup, exact card save, separate exposure, streamed task, cancellation without saved turn, evidence report, retry memory, restart, responsive widths.');
}finally{if(browser)await browser.close();server.kill();}})().catch(e=>{console.error('Fixture DB: '+data);console.error(e);process.exitCode=1;});
