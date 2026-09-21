/* Real local RPC and isolated storage; labeled AI fixtures, no paid calls. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const data=fs.mkdtempSync(path.join(os.tmpdir(),'haru-daily-word-'));
const script=`import sys,copy,time
sys.path[:0]=['backend','scripts']
import service
from curriculum import CARDS
from preview import Handler,ThreadingHTTPServer
index=0
def fixture(task,context,schema):
 global index
 index+=1
 time.sleep(0.3)
 return dict(copy.deepcopy(CARDS[0]),word=['猫','雨','花'][index-1],reading='テスト',meaning='界面测试内容'), 'ui-fixture'
service.generate=fixture
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
print(server.server_port,flush=True)
server.serve_forever()`;
const server=spawn(path.join(root,'.venv/bin/python'),['-u','-c',script],{cwd:root,env:{...process.env,HARU_DATA_DIR:data},stdio:['ignore','pipe','inherit']});
(async()=>{
 let browser;
 try{
  const port=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('preview timeout')),10000);server.stdout.once('data',d=>{clearTimeout(timer);resolve(Number(d.toString().trim()));});server.once('error',reject);});
  browser=await chromium.launch({headless:true,channel:process.env.HARU_BROWSER_CHANNEL||'chrome'});
  const page=await browser.newPage({viewport:{width:1360,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  let calls=0,fail=false,escape=false;
  await page.route('**/rpc',async route=>{
   if(route.request().postDataJSON().action!=='daily_word')return route.continue();
   calls++;
   if(fail)return route.fulfill({json:{ok:false,error:'模拟网络失败，请重试。'}});
   if(escape)return route.fulfill({json:{ok:true,data:{word:'<img src=x onerror=alert(1)>',reading:'テスト',romaji:'test',meaning:'<script>test</script>',model:'fixture'}}});
   return route.continue();
  });
  await page.goto(`http://127.0.0.1:${port}`);
  await page.locator('#daily-word [role=status]').waitFor();
  assert.equal(await page.locator('.little-note').count(),0);
  await page.locator('#daily-word .word-kana').filter({hasText:'猫'}).waitFor();
  assert.equal(await page.locator('#daily-word [data-speak]').getAttribute('data-speak'),'猫');
  await page.locator('#nav [data-nav=grammar_library]').click();
  await page.locator('#nav [data-nav=home]').click();
  assert.equal(calls,1);
  await page.getByRole('button',{name:'＋ 加入词卡',exact:true}).click();
  await page.getByRole('button',{name:'已加入词卡',exact:true}).waitFor();
  const cards=await page.evaluate(()=>rpc('cards'));
  assert.deepEqual(cards.map(c=>c.word),['猫']);assert.equal(calls,1);
  await page.reload();
  await page.locator('#daily-word .word-kana').filter({hasText:'雨'}).waitFor();
  assert.equal(calls,2);
  fail=true;await page.reload();
  await page.locator('#daily-word [role=alert]').waitFor();
  assert.equal(await page.locator('#daily-word [data-speak]').count(),0);
  await page.locator('#nav [data-nav=grammar_library]').click();
  await page.locator('#nav [data-nav=home]').click();
  assert.equal(calls,3);
  fail=false;await page.getByRole('button',{name:'重新生成',exact:true}).click();
  await page.locator('#daily-word .word-kana').filter({hasText:'花'}).waitFor();
  assert.equal(calls,4);
  for(const width of [840,1024,1360]){
   await page.setViewportSize({width,height:1000});
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow at ${width}`);
  }
  fs.mkdirSync(path.join(root,'runtime/validation/daily-word'),{recursive:true});
  await page.screenshot({path:path.join(root,'runtime/validation/daily-word/home.png'),fullPage:true});
  escape=true;await page.reload();
  await page.locator('#daily-word .word-kana').waitFor();
  assert.equal(await page.locator('#daily-word img, #daily-word script').count(),0);
  assert.ok((await page.locator('#daily-word .word-kana').innerText()).includes('<img'));
  assert.deepEqual(errors,[]);
  console.log('Daily word UI passed: loading, sidebar removal, per-open generation, navigation reuse, speech text, exact card save, failure/retry, escaping, 3 widths.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error('Isolated test data: '+data);console.error(e);process.exitCode=1;});
