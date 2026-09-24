'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const desktop=fs.readFileSync('ui/desktop.js','utf8');
const app=fs.readFileSync('ui/app.js','utf8').split("$('#settings-nav').innerHTML=")[0];
const study=fs.readFileSync('ui/study.js','utf8');
function harness(platform){
 const calls=[],toasts=[],nodes=new Map();let browserCalls=0;
 const forbidden=()=>{browserCalls++;throw new Error('browser speech must not be used');};
 const speechSynthesis={getVoices:forbidden,speak:forbidden,cancel:forbidden};
 const window={speechSynthesis},response={current:{ok:true,data:{cached:true}}};
 const copy=value=>JSON.parse(JSON.stringify(value));
 if(platform==='macOS')window.webkit={messageHandlers:{haru:{postMessage(message){calls.push(copy(message));queueMicrotask(()=>window.haruResolve(message.id,response.current));}}}};
 if(platform==='Windows')window.pywebview={api:{request:async message=>{calls.push(copy(message));return response.current;}}};
 const context=vm.createContext({window,distributionLocked:false,console,setTimeout,clearTimeout,setInterval(){},
  navigator:{userAgent:platform==='Windows'?'HaruDesktop/Windows':'test'},location:{protocol:'http:'},
  fetch(){throw new Error('preview must not request speech over HTTP');},speechSynthesis,SpeechSynthesisUtterance:forbidden,
  localStorage:{getItem(){return null;},setItem(){},removeItem(){}},
  document:{addEventListener(){},querySelector(selector){if(!nodes.has(selector))nodes.set(selector,{textContent:'',hidden:false});return nodes.get(selector);}},
  recordToast:(message,error)=>toasts.push({message,error})});
 vm.runInContext(desktop,context);vm.runInContext(app,context);vm.runInContext(study,context);
 vm.runInContext("toast=recordToast;page='jlpt'",context);
 return {calls,toasts,response,browserCalls:()=>browserCalls,run:code=>vm.runInContext(code,context)};
}
async function main(){
 for(const platform of ['macOS','Windows']){
  const h=harness(platform);
  await h.run("speakText('雨が降っています。')");
  assert.equal(h.calls.length,1);assert.equal(h.calls[0].action,'speak');
  assert.deepEqual(h.calls[0].params,{text:'雨が降っています。',rate:0.42});
  assert.equal(h.toasts.length,0,'cached native playback should succeed without a browser voice');
  await h.run("state={profile:{speech_rate:1.5}};speakText('速い朗読')");
  assert.deepEqual(h.calls[1].params,{text:'速い朗読',rate:0.63});
  h.response.current={ok:false,error:'此内容尚未缓存，请联网后重试。'};
  await h.run("speakText('新しい文')");
  assert.deepEqual(h.toasts,[{message:'此内容尚未缓存，请联网后重试。',error:true}]);
  assert.equal(h.browserCalls(),0,'native failure must never trigger browser/system speech');
  h.response.current={ok:true,data:{}};
  await h.run("studyAction({dataset:{study:'stop-audio'}})");
  assert.equal(h.calls.at(-1).action,'study_stop_audio');
  assert.equal(h.browserCalls(),0);
 }
 const preview=harness('preview');
 await preview.run("speakText('こんにちは。')");
 await preview.run("studyAction({dataset:{study:'stop-audio'}})");
 assert.equal(preview.calls.length,0);assert.equal(preview.browserCalls(),0);
 assert.equal(preview.toasts.length,2);
 for(const notice of preview.toasts)assert.match(notice.message,/Haru 桌面 App/);
 console.log('macOS/Windows speech RPC, native errors and preview-without-browser-speech checks passed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
