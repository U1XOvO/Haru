/* Real RPC, temporary .env and SQLite; ping verifies saved config using fixture AI. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const data=fs.mkdtempSync(path.join(os.tmpdir(),'haru-ai-config-'));
const script=`import sys,os
from pathlib import Path
sys.path[:0]=['backend','scripts']
import llm,service
from preview import Handler,ThreadingHTTPServer
llm.ROOT=Path(os.environ['HARU_DATA_DIR'])
for key in llm.CONFIG_KEYS: os.environ.pop(key,None)
def fixture(task,context,schema):
 if schema=={'ok':True}:
  config=llm.configuration()
  if config['model']=='fail-fixture': raise llm.AppError('模拟连接失败')
  if config['key']!='fixture-key': raise llm.AppError('未读取已保存密钥')
  return {'ok':True},config['model']
 raise llm.AppError('离线界面验证不调用 AI')
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
  const page=await browser.newPage({viewport:{width:1360,height:1050}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  let saves=0,pings=0,failSave=false;
  await page.route('**/rpc',async route=>{
   const req=route.request().postDataJSON();
   if(req.action==='config_save'){saves++;if(failSave)return route.fulfill({json:{ok:false,error:'模拟保存权限错误'}});}
   if(req.action==='ping')pings++;
   return route.continue();
  });
  await page.goto(`http://127.0.0.1:${port}`);
  await page.getByRole('button',{name:'偏好设置',exact:true}).click();
  const form=page.locator('#ai-config-form'),key=form.locator('[name=key]'),model=form.locator('[name=model]');
  await form.waitFor();
  assert.equal(await key.getAttribute('type'),'password');
  assert.equal(await key.inputValue(),'');
  await form.locator('[name=base]').fill('https://example.com/v1');
  await model.fill('ui-fixture');await key.fill('fixture-key');
  await form.locator('[name=timeout]').fill('25');
  await form.getByRole('button',{name:'保存配置',exact:false}).click();
  await page.getByText('已保存到 .env，后续请求自动使用。',{exact:true}).waitFor();
  assert.equal(pings,0);assert.equal(await key.inputValue(),'');
  assert.match(fs.readFileSync(path.join(data,'.env'),'utf8'),/LLM_API_KEY='fixture-key'/);
  // Blank key is retained, and ping uses the newly saved model.
  await model.fill('second-fixture');
  await form.getByRole('button',{name:'保存并测试连接',exact:true}).click();
  await page.getByText('配置已保存，连接成功 · 实际返回模型：second-fixture',{exact:true}).waitFor();
  assert.equal(pings,1);
  await page.reload();await page.getByRole('button',{name:'偏好设置',exact:true}).click();await form.waitFor();
  assert.equal(await model.inputValue(),'second-fixture');assert.equal(await key.inputValue(),'');
  assert.equal(await form.locator('[name=timeout]').inputValue(),'25');
  await model.fill('fail-fixture');await form.getByRole('button',{name:'保存并测试连接',exact:true}).click();
  await page.getByText('配置已保存；测试连接失败：模拟连接失败',{exact:true}).waitFor();
  assert.match(fs.readFileSync(path.join(data,'.env'),'utf8'),/LLM_MODEL_ID='fail-fixture'/);
  assert.equal(await form.locator('fieldset').isDisabled(),false);
  // Invalid backend input leaves original bytes; drafts survive a write failure.
  const original=fs.readFileSync(path.join(data,'.env'),'utf8');
  await form.locator('[name=base]').fill('http://example.com');
  await form.getByRole('button',{name:'保存配置',exact:false}).click();
  await page.locator('#ping-result').filter({hasText:'配置未保存：LLM_BASE_URL'}).waitFor();
  assert.equal(fs.readFileSync(path.join(data,'.env'),'utf8'),original);
  await form.locator('[name=base]').fill('https://example.com/v1');
  failSave=true;await model.fill('retry-fixture');await key.fill('replacement-fixture');
  await form.getByRole('button',{name:'保存配置',exact:false}).click();
  await page.getByText('配置未保存：模拟保存权限错误',{exact:true}).waitFor();
  assert.equal(await model.inputValue(),'retry-fixture');assert.equal(await key.inputValue(),'replacement-fixture');
  assert.equal(fs.readFileSync(path.join(data,'.env'),'utf8'),original);
  failSave=false;await form.getByRole('button',{name:'刷新配置',exact:false}).click();
  await page.waitForFunction(()=>document.querySelector('#ai-config-form [name=model]')?.value==='fail-fixture');
  assert.equal(await key.inputValue(),'');
  // External file edits are reloaded and safely escaped in inputs.
  fs.appendFileSync(path.join(data,'.env'),`\nLLM_MODEL_ID='<img src=x onerror=alert(1)>'\n`);
  await form.getByRole('button',{name:'刷新配置',exact:false}).click();
  await page.waitForFunction(()=>document.querySelector('#ai-config-form [name=model]')?.value.startsWith('<img'));
  assert.equal(await form.locator('img').count(),0);
  await model.fill('ui-fixture');
  const screenshots=path.join(root,'runtime/validation/ai-config');fs.mkdirSync(screenshots,{recursive:true});
  for(const width of [840,1024,1360]){
   await page.setViewportSize({width,height:1050});
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow at ${width}`);
   await page.screenshot({path:path.join(screenshots,`settings-${width}.png`),fullPage:true});
  }
  assert.equal(saves,5);assert.deepEqual(errors,[]);
  console.log('AI settings UI passed: create, reload, blank-key retention, save-before-ping, validation, write/ping failure recovery, safe escaping, 3 widths; temporary .env, fixture AI only.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error('Isolated test data: '+data);console.error(e);process.exitCode=1;});
