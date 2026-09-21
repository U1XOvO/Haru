'use strict';
// Additive learning tools. Existing whole-sentence reading/translation stays visible.
const annotationCache=new Map();
let dictionaryItem=null,chatState=null,chatMessages=[],chatFlight=null,chatBusy=false,chatLoadRun=0,dictionaryRun=0;
const viewedSources=new Set();
function learningReadContext(){return {page,nav:navigationRun,ref:page==='lessons'?lesson?.id:page==='chat'?scene:page==='immersion'?story?.id:null};}
function learningReadCurrent(context){return context.page===page&&context.nav===navigationRun&&context.ref===(page==='lessons'?lesson?.id:page==='chat'?scene:page==='immersion'?story?.id:null);}

function readingHTML(sentence){
 const a=annotationCache.get(sentence)||state?.annotations?.find(x=>x.sentence===sentence);
 if(!a)return esc(sentence);
 return a.tokens.map(t=>t.lemma?`<button type="button" class="reading-word" data-lookup="${esc(t.surface)}" data-sentence="${esc(sentence)}" title="查看 ${esc(t.lemma)}"><ruby>${esc(t.surface)}${t.reading&&t.reading!==t.surface?`<rt>${esc(t.reading)}</rt>`:''}</ruby></button>`:esc(t.surface)).join('');
}
function japanese(sentence){return `<span data-reading-text="${esc(sentence)}">${readingHTML(sentence)}</span> <button type="button" class="reading-action" data-annotate="${esc(sentence)}" title="AI 逐词标注，结果缓存；读音和分词可能需要核实" aria-label="逐词注音：${esc(sentence)}">逐词注音</button>`;}
function reviewCoverage(d){
 if(!d?.review_targets?.length)return '';
 return `<div class="review-targets hint">本次复习词：${d.review_targets.map(t=>`${esc(t.word)} · ${t.covered?'已在日语原文复现':'本次未复现'}`).join(' / ')}<br>复现只记录接触；到期复习仍需在词卡中完成。</div>`;
}
function pronunciationHTML(p){
 if(!p)return '';
 return `<details class="pronunciation"><summary>拍数与声调辅助</summary><p>${p.count?`${p.count} 拍 · ${p.moras.map(esc).join(' · ')}`:'读音尚不能可靠分拍'}</p>${p.nucleus!==null?`<div class="pitch-line" aria-label="单独发音示意：${p.pattern.join('、')}">${p.moras.map((m,i)=>`<span class="pitch-mora ${p.pattern[i]==='高'?'high':'low'}">${esc(m)}${p.nucleus===i+1?'↘':''}</span>`).join('')}</div><p class="hint">${p.nucleus===0?'无下降核（平板型）':'第 '+p.nucleus+' 拍后下降'} · 单独发音示意，句中高低会变化。</p><button type="button" class="text-link" data-url="${esc(p.source)}">来源：${esc(p.source_label)}</button>`:'<p class="hint">此词声调暂无已核实数据，不依据 AI 猜测。可对照系统朗读；此处不进行发音评分。</p>'}<p class="hint">拗音合为一拍；ん、っ、ー各占一拍。声调示例目前收录「雨」「飴」。</p></details>`;
}
function knowledgeHTML(){
 const items=state.knowledge||[];
 return `<section class="card knowledge-panel"><h3>词汇接触与复习</h3><p class="hint">接触按不同课程／故事／对话回复计数，重复打开不重复累计；查词按词条与原句去重。回忆记录来自词卡自评，不是能力认证。</p><div class="knowledge-scroll"><table><thead><tr><th>词语</th><th>接触</th><th>查词</th><th>主动复习</th><th>自评记起</th></tr></thead><tbody>${items.slice(0,100).map(w=>`<tr><td>${esc(w.word)}</td><td>${w.exposure}</td><td>${w.lookup}</td><td>${w.reviews}</td><td>${w.recalled}</td></tr>`).join('')||'<tr><td colspan="5">尚无记录。阅读内容、查词或复习后会显示。</td></tr>'}</tbody></table></div>${items.length>100?'<p class="hint">显示前100项，完整记录包含在学习档案导出中。</p>':''}</section>`;
}
async function noteExposure(ref){
 if(!ref||viewedSources.has(ref))return;
 viewedSources.add(ref);
 try{await rpc('encounter',{ref});}catch(e){viewedSources.delete(ref);toast('词汇接触记录未保存：'+e.message,true);}
}
function enhanceLearning(){
 if(page==='chat'&&!chatState)chatMessages=[];
 if(page==='progress')$('#main').insertAdjacentHTML('beforeend',studySummaryHTML()+knowledgeHTML());
 if(page==='lessons'&&lesson&&$('#main [data-reading-text]'))void noteExposure(lesson.id);
 if(page==='immersion'&&story)void noteExposure(story.id);
 if(page==='chat'&&chatMessages.length)paintChat();
}
async function openDictionary(word,sentence=''){
 const request=++dictionaryRun,context=learningReadContext();
 await run('正在查词…',async()=>{
  const result=await rpc('dictionary',{word,sentence});if(request!==dictionaryRun||!learningReadCurrent(context))return;dictionaryItem=result;const d=dictionaryItem;
  $('#modal-content').innerHTML=`<h2>${esc(d.word)}</h2>${d.queried!==d.word?`<p class="hint">原文 ${esc(d.queried)} → 辞书形 ${esc(d.word)}</p>`:''}<p class="reading">${esc(d.reading)} <span class="romaji">${esc(d.romaji)}</span></p><p>${esc(d.meaning)}</p>${d.context_meaning?`<p class="hint">本句用法：${esc(d.context_meaning)}</p>`:''}<p class="jp">${esc(d.example)}</p><p>${esc(d.translation)}</p>${pronunciationHTML(d.pronunciation)}${source(d)}<div class="inline-actions">${speak(d.word,true)}<button type="button" class="btn primary" data-dictionary-add="${esc(d.word)}">加入词卡</button><button type="button" class="btn" data-action="close-modal">关闭</button></div>`;
  if(!$('#modal').open)$('#modal').showModal();
 });
}
document.addEventListener('click',async e=>{
 const b=e.target.closest('button');if(!b)return;
 if(b.dataset.annotate){
  const context=learningReadContext();
  await run('正在生成逐词注音…',async()=>{
   const a=await rpc('annotate',{text:b.dataset.annotate});if(!learningReadCurrent(context))return;annotationCache.set(a.sentence,a);
   document.querySelectorAll('[data-reading-text]').forEach(el=>{if(el.dataset.readingText===a.sentence)el.innerHTML=readingHTML(a.sentence);});
   b.textContent='已注音 · 可点词查阅';
   const refs=page==='lessons'?[lesson?.id]:page==='immersion'?[story?.id]:page==='chat'?chatMessages.filter(m=>m.role==='assistant'&&m.jp===a.sentence).map(m=>'message:'+m.message_id):[];
   for(const ref of refs.filter(Boolean)){viewedSources.delete(ref);await noteExposure(ref);}
  },b);return;
 }
 if(b.dataset.lookup){await openDictionary(b.dataset.lookup,b.dataset.sentence||'');return;}
 if(b.dataset.dictionaryAdd){await run('正在加入词卡…',async()=>{await rpc('dictionary_add',{word:b.dataset.dictionaryAdd});await refresh();b.disabled=true;b.textContent='已加入词卡';toast('已加入词卡，相同词条不重复保存。');});return;}
 if(b.dataset.chatAction){
  const a=b.dataset.chatAction;
  if(a==='stop'){await stopChat();return;}
  if(chatBusy||chatFlight)return;
  const selected=scene;chatBusy=true;
  try{await run(a==='finish'?'正在对照原话点评…':'正在准备场景任务…',async()=>{
   if(a==='finish')await rpc('chat_finish',{session:chatState.session});
   else await rpc('chat_start',{scene:selected,retry:a==='retry'});
   if(page==='chat'&&scene===selected)await loadChat();
  },b);}finally{chatBusy=false;if(page==='chat')paintTask();}
 }
});
document.addEventListener('submit',async e=>{
 if(e.target.id!=='lookup-form')return;e.preventDefault();
 const word=$('#lookup-word').value.trim();if(word)await openDictionary(word);
});

function chatView(){
 const info=scenes.find(x=>x[0]===scene);
 return title('真实对话','在一个小任务中练习表达，也可以自由聊天。')+`<div class="chat-layout"><aside><div class="chat-scenes">${scenes.map(([id,i,t,s])=>`<button class="scene-btn ${scene===id?'active':''}" data-scene="${id}">${icon(i)}<div>${t}<small>${s}</small></div></button>`).join('')}</div><div class="tip-note"><strong>你可以放心说错</strong>保留中文提示和温和纠错。任务点评只用于本轮对话，不加入错题复习。</div><form id="lookup-form" class="card"><label for="lookup-word">随手查词</label><input id="lookup-word" maxlength="100" placeholder="输入日语词语" required><button class="btn small" type="submit">查词</button></form></aside><section class="chat-window"><div class="chat-head"><div class="avatar">は</div><div><h3>Haru · ${info[2]}</h3><p>文字练习 · 日语朗读</p></div></div><div id="chat-task"></div><div class="chat-messages" id="chat-messages">${empty('欢迎来到'+info[2],'开始一个场景任务，或直接输入一句话。')}</div><form id="chat-form" class="chat-form"><textarea id="chat-input" placeholder="试着说：こんにちは。" maxlength="2000" required>${esc(drafts.chat)}</textarea><div class="form-row"><small>日语 / 中文 · ⌘ Enter 发送</small><button class="btn primary" type="submit" ${chatFlight?'disabled':''}>发送 ${icon('arrow')}</button><button class="btn" type="button" data-chat-action="stop" ${chatFlight?'':'hidden'}>停止生成</button></div></form></section></div>`;
}
function paintTask(){
 if(!$('#chat-task')||!chatState)return;
 const s=chatState,report=s.report;
 $('#chat-task').innerHTML=`<div class="task-panel"><h3>${s.status==='free'?'可选场景任务':s.status==='finished'?'本轮任务点评':'本轮目标'}</h3><ol>${s.goals.map(g=>`<li>${esc(g)}</li>`).join('')}</ol>${report?`<p>${esc(report.summary)}</p>${report.goals.map(g=>`<div class="task-result"><b>${g.met?'已有表达证据':'还可练习'} · ${esc(g.goal)}</b>${g.quote?`<blockquote>${esc(g.quote)}</blockquote>`:''}<p>${esc(g.note)}</p></div>`).join('')}<p class="hint">AI 点评 · 引文已核对原话，语义判断仍可能有误。不是考试或能力认证。</p>`:''}<div class="inline-actions">${s.status==='free'?'<button type="button" class="btn small soft" data-chat-action="start">开始场景任务</button>':s.status==='active'?'<button type="button" class="btn small soft" data-chat-action="finish">结束并点评</button>':''}${s.status!=='free'?'<button type="button" class="btn small" data-chat-action="retry">再练一次</button>':''}</div>${s.memory?.pending_task?`<details><summary>继续上次的问题</summary><p>${esc(s.memory.pending_task)}</p></details>`:''}${s.memory?.previous?.length?`<details><summary>此前任务记忆（我的原话）</summary>${s.memory.previous.map(m=>`<p class="hint">${esc(m.excerpt)} · 消息 ${m.message_id}</p>`).join('')}</details>`:''}${s.memory?.summary?.length?`<details><summary>早前对话记忆（原话节选）</summary>${s.memory.summary.map(m=>`<p class="hint">${m.role==='user'?'我':'Haru'}：${esc(m.excerpt)} · 消息 ${m.message_id}</p>`).join('')}</details>`:''}</div>`;
 $('#chat-task').querySelectorAll('button').forEach(b=>b.disabled=!!chatFlight||chatBusy);
}
function messagesHTML(messages){return messages.map(m=>m.role==='user'?`<div class="message user">${esc(m.text)}</div>`:`<div class="message"><div class="message-jp"><p class="jp" lang="ja">${japanese(m.jp)}</p>${speak(m.jp)}</div><p class="reading">${esc(m.kana)} <span class="romaji">· ${esc(m.romaji)}</span></p><p class="translation">${esc(m.zh)}</p><div class="feedback">${esc(m.feedback)}</div><p class="suggestion">可以试着回答：${esc(m.suggestion)}</p>${reviewCoverage(m)}</div>`).join('');}
function paintChat(){
 if(!$('#chat-messages'))return;
 $('#chat-messages').innerHTML=messagesHTML(chatMessages);
 if(chatFlight&&chatFlight.scene===scene){
  $('#chat-messages').insertAdjacentHTML('beforeend',`<div class="message user">${esc(chatFlight.message)}</div><div class="message stream-draft"><p class="jp" id="stream-text">${esc(chatFlight.text||'正在连接…')}</p><small>生成中，完整校验后保存</small></div>`);
 }
 $('#chat-messages').scrollTop=99999;paintTask();
 const submit=$('#chat-form button[type=submit]');if(submit)submit.disabled=!!chatFlight||chatState?.status==='finished';
 const stop=$('[data-chat-action=stop]');if(stop)stop.hidden=!chatFlight;
 for(const m of chatMessages)if(m.role==='assistant'&&m.message_id)void noteExposure('message:'+m.message_id);
}
async function loadChat(){
 const selected=scene,request=++chatLoadRun,nav=navigationRun;
 await run('正在找回对话…',async()=>{
  const current=await rpc('chat_state',{scene:selected});
  const [messages,memory]=await Promise.all([rpc('chat_history',{session:current.session}),rpc('chat_memory',{session:current.session})]);
  if(request===chatLoadRun&&nav===navigationRun&&page==='chat'&&scene===selected){chatState={...current,memory};chatMessages=messages;paintChat();}
 });
}
window.haruStream=(id,event)=>pending.get(id)?.onEvent?.(event);
async function streamingRPC(params,onEvent){
 if(haruHasNative()){
  return new Promise((resolve,reject)=>{const id=++reqId;const timer=setTimeout(()=>{pending.delete(id);void rpc('chat_cancel',{request_id:params.request_id});reject(new Error('对话超时，本轮已停止。'));},610000);pending.set(id,{resolve,reject,timer,onEvent});haruPostMessage({id,action:'chat_stream',params});});
 }
 const controller=new AbortController();if(chatFlight)chatFlight.controller=controller;
 const response=await fetch('/rpc',{signal:controller.signal,method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'chat_stream',params})});
 if(!response.ok||!response.body)throw new Error('对话连接失败。');
 const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',result;
 try{while(true){const {value,done}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});let at;while((at=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,at);buffer=buffer.slice(at+1);if(!line.trim())continue;const event=JSON.parse(line);if(event.type==='delta')onEvent(event);else result=event;}if(done)break;}}finally{reader.releaseLock();}
 if(!result?.ok)throw new Error(result?.error||'对话中断，本轮未保存。');
 return result.data;
}
async function sendChat(){
 if(chatFlight||chatBusy)return;
 if(!chatState){toast('场景尚未加载，请稍后再试。',true);return;}
 const message=$('#chat-input').value.trim();if(!message)return;
 const flight={request_id:crypto.randomUUID(),scene,session:chatState.session,message,text:'',cancelled:false};chatFlight=flight;paintChat();
 try{
  await streamingRPC({session:flight.session,message,request_id:flight.request_id},event=>{flight.text=event.text;if(page==='chat'&&scene===flight.scene){const el=$('#stream-text');if(el)el.textContent=event.text;}});
  if(drafts.chat===message){drafts.chat='';if(page==='chat'&&scene===flight.scene)$('#chat-input').value='';}
  await refresh();
 }catch(e){toast(flight.cancelled?'已停止生成，输入已保留。':e.message,true);}
 finally{chatFlight=null;if(page==='chat')await loadChat();}
}
async function stopChat(){
 const flight=chatFlight;if(!flight)return;
 await run('正在停止生成…',async()=>{const result=await rpc('chat_cancel',{request_id:flight.request_id});flight.cancelled=result.status==='cancelled';if(flight.cancelled)flight.controller?.abort();if(result.status==='complete')toast('回复已完整保存。');});
}
