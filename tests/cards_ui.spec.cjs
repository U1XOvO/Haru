/* Full local RPC flow; AI is replaced with labeled fixtures, never a paid call. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const data=fs.mkdtempSync(path.join(os.tmpdir(),'haru-cards-ui-'));
const script=`import sys,copy
sys.path[:0]=['backend','scripts']
import service
from curriculum import CARDS
from preview import Handler,ThreadingHTTPServer
words=['猫','犬','駅','空','雨','山','川','海','花','本','車','家','店','道','傘','靴','服','朝','夜','昼']
index=0
def fixture(task,context,schema):
 global index
 cards=[]
 for _ in range(context['count']):
  cards.append(dict(copy.deepcopy(CARDS[0]),word=words[index],meaning='界面测试词义'))
  index+=1
 return {'cards':cards},'ui-fixture'
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
  const page=await browser.newPage({viewport:{width:1360,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${port}`);
  await page.getByRole('button',{name:'即时学习卡',exact:true}).click();
  await page.getByText('还没有词卡。生成后会自动保存在这里。',{exact:true}).waitFor();
  for(const [count,total] of [[5,5],[10,15],[1,16]]){
   await page.getByLabel('随机生成数量',{exact:true}).selectOption(String(count));
   await page.getByRole('button',{name:'随机生成新词卡',exact:false}).click();
   await page.getByRole('heading',{name:`本次新增 ${count} 张`,exact:true}).waitFor();
   assert.equal(await page.locator('#card-history .history-card').count(),total);
   assert.equal(await page.locator('.card-detail .word').count(),1);
   assert.equal(await page.locator('.review-buttons').count(),0);
  }
  assert.equal(new Set(await page.locator('#card-history b').allTextContents()).size,16);
  await page.getByLabel('搜索历史单词卡').fill('猫');
  assert.equal(await page.locator('#card-history .history-card').count(),1);
  await page.getByRole('button',{name:'查看词卡：猫',exact:true}).click();
  await page.locator('.card-detail .word').filter({hasText:'猫'}).waitFor();
  await page.getByLabel('搜索历史单词卡').fill('不存在的词');
  await page.getByText('没有找到匹配的词卡。',{exact:true}).waitFor();
  await page.getByLabel('搜索历史单词卡').fill('');
  await page.getByRole('button',{name:'返回到期复习',exact:true}).click();
  const reviewedWord=await page.locator('button.flashcard .word').innerText();
  await page.locator('button.flashcard').click();
  await page.getByRole('button',{name:'记住了',exact:false}).click();
  await page.waitForFunction(()=>document.querySelector('.card-title')?.textContent.includes('15 张到期'));
  const snapshot=await page.evaluate(()=>rpc('cards',{order:'recent'}));
  await page.getByRole('button',{name:`查看词卡：${reviewedWord}`,exact:true}).click();
  await page.getByRole('heading',{name:'单词卡详情',exact:true}).waitFor();
  assert.deepEqual(await page.evaluate(()=>rpc('cards',{order:'recent'})),snapshot);
  await page.reload();await page.locator('#nav [data-nav=cards]').click();
  await page.waitForFunction(()=>document.querySelectorAll('#card-history .history-card').length===16);
  await page.getByRole('button',{name:`查看词卡：${reviewedWord}`,exact:true}).click();
  await page.getByRole('heading',{name:'单词卡详情',exact:true}).waitFor();
  await page.route('**/rpc',async route=>{
   if(route.request().postDataJSON().action==='card_random')await route.fulfill({json:{ok:false,error:'模拟连接失败，已有记录已保留。'}});
   else await route.continue();
  });
  await page.getByRole('button',{name:'随机生成新词卡',exact:false}).click();
  await page.locator('#toast.error').waitFor();
  assert.equal(await page.locator('#card-history .history-card').count(),16);
  assert.equal(await page.getByRole('button',{name:'随机生成新词卡',exact:false}).isEnabled(),true);
  for(const width of [840,1024,1360]){
   await page.setViewportSize({width,height:1000});
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow at ${width}`);
  }
  fs.mkdirSync(path.join(root,'runtime/validation/cards'),{recursive:true});
  await page.screenshot({path:path.join(root,'runtime/validation/cards/history.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  console.log('Cards UI passed: 1/5/10 unique batches, saved detail, search, review isolation, reload persistence, error recovery, 3 responsive widths.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error('Isolated test data: '+data);console.error(e);process.exitCode=1;});
