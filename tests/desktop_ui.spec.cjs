/* Offline UI check for both desktop transports; native devices are mocked. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const {spawn}=require('node:child_process');
const os=require('node:os');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const python=path.join(root,process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python');
const data=fs.mkdtempSync(path.join(os.tmpdir(),'haru-desktop-ui-'));

(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.HARU_TEST_BROWSER?{executablePath:process.env.HARU_TEST_BROWSER}:{})});
 try{
  for(const transport of ['windows','macos']){
   const context=await browser.newContext({userAgent:transport==='windows'?'HaruDesktop/Windows':'HaruTestMac',viewport:{width:1280,height:900}});
   const page=await context.newPage();const errors=[],actions=[];
   page.on('pageerror',e=>errors.push(e.message));
   await page.exposeFunction('desktopRequest',message=>new Promise((resolve,reject)=>{
    actions.push(message.action);
    if(message.action==='study_pick'){resolve({ok:true,data:{cancelled:true}});return;}
    if(['record_start','record_stop','record_play','speak','study_stop_audio'].includes(message.action)){resolve({ok:true,data:{}});return;}
    const child=spawn(python,['-u',path.join(root,'tests/windows_fixture_bridge.py')],{cwd:root,env:{...process.env,HARU_DATA_DIR:path.join(data,transport),PYTHONIOENCODING:'utf-8'},stdio:['pipe','pipe','pipe']});
    let buffer='',result,stderr='';const events=[];
    child.stdout.on('data',chunk=>{buffer+=chunk.toString('utf8');let index;while((index=buffer.indexOf('\n'))>=0){const event=JSON.parse(buffer.slice(0,index));buffer=buffer.slice(index+1);if(event.type==='delta')events.push(page.evaluate(({id,event})=>window.haruStream(id,event),{id:message.id,event}));else result=event;}});
    child.stderr.on('data',chunk=>stderr+=chunk.toString());
    child.on('error',reject);child.on('close',async code=>{await Promise.all(events);code===0?resolve(result):reject(new Error(stderr||'fixture bridge failed'));});
    child.stdin.end(JSON.stringify(message));
   }));
   await page.addInitScript(({transport})=>{
    if(transport==='macos')window.webkit={messageHandlers:{haru:{postMessage:message=>window.desktopRequest(message).then(result=>window.haruResolve(message.id,result))}}};
    else setTimeout(()=>{window.pywebview={api:{request:window.desktopRequest}};window.dispatchEvent(new Event('pywebviewready'));},300);
   },{transport});
   await page.goto(pathToFileURL(path.join(root,'ui/index.html')).href);
   await page.waitForSelector('[data-nav="cards"]');
   assert.equal(await page.evaluate(()=>haruHasNative()),true);
   await page.evaluate(async()=>{await rpc('card_seed');await navigate('cards');});
   assert.ok(await page.locator('#main').innerText());
   const stream=await page.evaluate(async()=>{const session=await rpc('chat_state',{scene:'cafe'});let delta='';await streamingRPC({session:session.session,message:'こんにちは',request_id:crypto.randomUUID()},event=>delta=event.text);return delta;});
   assert.match(stream,/日本語/);
   await page.evaluate(async()=>{await speakText('こんにちは');await rpc('record_start');await rpc('record_stop');await rpc('record_play');await rpc('study_pick');await rpc('study_stop_audio');});
   await page.evaluate(()=>navigate('lessons'));
   assert.ok(!actions.includes('study_generate'));
   assert.deepEqual(errors,[]);
   console.log(`PASS ${transport}: delayed bridge readiness, bootstrap, cards, streaming, native action routing and lessons UI`);
   await context.close();
  }
 }finally{await browser.close();fs.rmSync(data,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
