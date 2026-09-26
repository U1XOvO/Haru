'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const desktop=fs.readFileSync('ui/desktop.js','utf8');
const app=fs.readFileSync('ui/app.js','utf8').split("$('#settings-nav').innerHTML=")[0];
const study=fs.readFileSync('ui/study.js','utf8');
function harness(platform){
 const calls=[],toasts=[],nodes=new Map(),timers=new Map();let browserCalls=0,timerId=0;
 const forbidden=()=>{browserCalls++;throw new Error('browser speech must not be used');};
 const speechSynthesis={getVoices:forbidden,speak:forbidden,cancel:forbidden};
 const window={speechSynthesis},response={current:{ok:true,data:{cached:true}}};
 const copy=value=>JSON.parse(JSON.stringify(value));
 const reply=message=>{calls.push(copy(message));return message.action==='speak'&&response.speak?response.speak:Promise.resolve(response.current);};
 if(platform==='macOS')window.webkit={messageHandlers:{haru:{postMessage(message){reply(message).then(result=>window.haruResolve(message.id,result));}}}};
 if(platform==='Windows')window.pywebview={api:{request:reply}};
 const context=vm.createContext({window,distributionLocked:false,console,
  setTimeout(callback,delay){const id=++timerId;timers.set(id,{callback,delay});return id;},clearTimeout(id){timers.delete(id);},setInterval(){},
  navigator:{userAgent:platform==='Windows'?'HaruDesktop/Windows':'test'},location:{protocol:'http:'},
  fetch(){throw new Error('preview must not request speech over HTTP');},speechSynthesis,SpeechSynthesisUtterance:forbidden,
  localStorage:{getItem(){return null;},setItem(){},removeItem(){}},
  document:{addEventListener(){},querySelector(selector){if(!nodes.has(selector))nodes.set(selector,{textContent:'',hidden:false});return nodes.get(selector);}},
  recordToast:(message,error)=>toasts.push({message,error})});
 vm.runInContext(desktop,context);vm.runInContext(app,context);vm.runInContext(study,context);
 vm.runInContext("toast=recordToast;page='jlpt'",context);
 return {calls,toasts,response,browserCalls:()=>browserCalls,run:code=>vm.runInContext(code,context),progress:(id,phase)=>window.haruProgress(id,{event:'audio',phase})};
}
async function streamingPlayback(platform){
 const h=harness(platform);h.run("page='immersion'");
 let finish;
 async function start(){
  h.response.speak=new Promise(resolve=>{finish=resolve;});
  const playback=h.run("speakText('長い物語です。')");
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(h.run('storyPlayback'),'preparing');
  return {playback,id:h.calls.at(-1).id};
 }
 let stream=await start();
 h.progress(stream.id,'chunk');
 assert.equal(h.run('storyPlayback'),'playing','the first chunk enables playback controls');
 const timer=h.run('storyAudioTimer');
 h.progress(stream.id,'chunk');
 assert.equal(h.run('storyAudioTimer'),timer,'later chunks must not postpone status polling');
 h.response.current={ok:true,data:{state:'paused'}};
 await h.run("handleAction('story-pause')");
 h.progress(stream.id,'chunk');
 assert.equal(h.run('storyPlayback'),'paused','new chunks must preserve an acknowledged pause');
 assert.match(h.run('storyAudioControls()'),/继续播放/);
 h.response.current={ok:true,data:{state:'playing'}};
 await h.run("handleAction('story-pause')");
 const resumedTimer=h.run('storyAudioTimer');
 h.progress(stream.id,'chunk');
 assert.equal(h.run('storyPlayback'),'playing');
 assert.equal(h.run('storyAudioTimer'),resumedTimer,'resumed playback keeps its polling schedule');
 h.response.current={ok:true,data:{state:'paused'}};
 await h.run("handleAction('story-pause')");
 finish({ok:true,data:{engine:'gemini'}});await stream.playback;
 assert.equal(h.run('storyPlayback'),'paused','finishing synthesis must not resume paused controls');
 assert.match(h.run('storyAudioControls()'),/继续播放/);
 h.response.current={ok:true,data:{state:'playing'}};
 await h.run("handleAction('story-pause')");
 assert.equal(h.run('storyPlayback'),'playing','completed streams can still resume');

 stream=await start();h.progress(stream.id,'chunk');
 const finalTimer=h.run('storyAudioTimer');
 finish({ok:true,data:{engine:'gemini'}});await stream.playback;
 assert.equal(h.run('storyAudioTimer'),finalTimer,'completion must not postpone an active status poll');

 stream=await start();h.progress(stream.id,'chunk');
 h.response.current={ok:true,data:{state:'paused'}};
 await h.run("handleAction('story-pause')");
 h.progress(stream.id,'reset');
 assert.equal(h.run('storyPlayback'),'preparing','fallback resets the discarded stream');
 finish({ok:true,data:{engine:'edge',fallback:'fixture'}});await stream.playback;
 assert.equal(h.run('storyPlayback'),'playing','fallback playback still starts normally');
 h.run("setStoryPlayback('idle')");
}
async function main(){
 for(const platform of ['macOS','Windows']){
  await streamingPlayback(platform);
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
  h.run("story={title:'雨の日',task:'空を見ます。',words:[],sentences:[{jp:'雨が降っています。',kana:'',romaji:'',zh:''},{jp:'傘を持ちます。',kana:'',romaji:'',zh:''}]};page='immersion';globalThis.japanese=s=>s;globalThis.reviewCoverage=()=>''");
  assert.match(h.run('immersionView()'),/id="story-audio-controls"/);
  h.response.current={ok:true,data:{engine:'edge'}};
  await h.run("handleAction('story-play')");
  assert.equal(h.calls.at(-1).action,'speak');
  assert.equal(h.calls.at(-1).params.text,'雨が降っています。傘を持ちます。');
  assert.match(h.run('storyAudioControls()'),/暂停播放/);
  h.response.current={ok:true,data:{state:'paused'}};
  await h.run("handleAction('story-pause')");
  assert.equal(h.calls.at(-1).action,'audio_toggle_pause');
  assert.match(h.run('storyAudioControls()'),/继续播放/);
  h.response.current={ok:true,data:{state:'playing'}};
  await h.run("handleAction('story-pause')");
  assert.match(h.run('storyAudioControls()'),/暂停播放/);
  h.response.current={ok:true,data:{state:'idle'}};
  await h.run("handleAction('story-pause')");
  assert.match(h.run('storyAudioControls()'),/data-action="story-pause" disabled/);
  h.response.current={ok:true,data:{engine:'edge'}};
  await h.run("speakText('短い文です。')");
  assert.match(h.run('storyAudioControls()'),/data-action="story-pause"[^]*暂停播放/);
  h.run("setStoryPlayback('idle')");
  assert.equal(h.browserCalls(),0);
 }
 const preview=harness('preview');
 await preview.run("speakText('こんにちは。')");
 await preview.run("studyAction({dataset:{study:'stop-audio'}})");
 assert.equal(preview.calls.length,0);assert.equal(preview.browserCalls(),0);
 assert.equal(preview.toasts.length,2);
 for(const notice of preview.toasts)assert.match(notice.message,/Haru 桌面 App/);
 console.log('macOS/Windows speech RPC, streaming pause/resume/completion/fallback, native errors and preview-without-browser-speech checks passed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
