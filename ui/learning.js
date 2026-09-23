'use strict';
// Additive learning tools. Existing whole-sentence reading/translation stays visible.
const annotationCache=new Map();
const annotationChecked=new Set();
let readingFlight=false;
let dictionaryRun=0;
const viewedSources=new Set();
function learningReadContext(){return {page,nav:navigationRun,ref:page==='lessons'?lesson?.id:page==='immersion'?story?.id:null};}
function learningReadCurrent(context){return context.page===page&&context.nav===navigationRun&&context.ref===(page==='lessons'?lesson?.id:page==='immersion'?story?.id:null);}

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
 return `<details class="pronunciation"><summary>拍数与声调辅助</summary><p>${p.count?`${p.count} 拍 · ${p.moras.map(esc).join(' · ')}`:'读音尚不能可靠分拍'}</p>${p.nucleus!==null?`<div class="pitch-line" aria-label="单独发音示意：${p.pattern.join('、')}">${p.moras.map((m,i)=>`<span class="pitch-mora ${p.pattern[i]==='高'?'high':'low'}">${esc(m)}${p.nucleus===i+1?'↘':''}</span>`).join('')}</div><p class="hint">${p.nucleus===0?'无下降核（平板型）':'第 '+p.nucleus+' 拍后下降'} · 单独发音示意，句中高低会变化。</p><button type="button" class="text-link" data-url="${esc(p.source)}">来源：${esc(p.source_label)}</button>`:'<p class="hint">此词声调暂无已核实数据，不依据 AI 猜测。可对照朗读音频；此处不进行发音评分。</p>'}<p class="hint">拗音合为一拍；ん、っ、ー各占一拍。声调示例目前收录「雨」「飴」。</p></details>`;
}
function knowledgeHTML(){
 const items=state.knowledge||[];
 return `<section class="card knowledge-panel"><h3>词汇接触与复习</h3><p class="hint">接触按不同课程或故事计数，重复打开不重复累计；查词按词条与原句去重。回忆记录来自词卡自评，不是能力认证。</p><div class="knowledge-scroll"><table><thead><tr><th>词语</th><th>接触</th><th>查词</th><th>主动复习</th><th>自评记起</th></tr></thead><tbody>${items.slice(0,100).map(w=>`<tr><td>${esc(w.word)}</td><td>${w.exposure}</td><td>${w.lookup}</td><td>${w.reviews}</td><td>${w.recalled}</td></tr>`).join('')||'<tr><td colspan="5">尚无记录。阅读内容、查词或复习后会显示。</td></tr>'}</tbody></table></div>${items.length>100?'<p class="hint">显示前100项，完整记录包含在学习档案导出中。</p>':''}</section>`;
}
async function noteExposure(ref){
 if(!ref||viewedSources.has(ref))return;
 viewedSources.add(ref);
 try{await rpc('encounter',{ref});}catch(e){viewedSources.delete(ref);toast('词汇接触记录未保存：'+e.message,true);}
}
function enhanceLearning(){
 if(page==='progress')$('#main').insertAdjacentHTML('beforeend',studySummaryHTML()+knowledgeHTML());
 if(page==='lessons'&&lesson&&$('#main [data-reading-text]'))void noteExposure(lesson.id);
 if(page==='immersion'&&story)void noteExposure(story.id);
 void loadVisibleReadings();
}
async function loadVisibleReadings(){
 if(readingFlight)return;
 const sentences=[...new Set([...document.querySelectorAll('[data-reading-text]')].map(el=>el.dataset.readingText))].filter(s=>s&&s.length<=1200&&!annotationChecked.has(s)&&!annotationCache.has(s)).slice(0,16);
 if(!sentences.length)return;
 readingFlight=true;
 let loaded=false;
 try{
  const result=await rpc('reading_lookup',{sentences});
  for(const s of result.checked)annotationChecked.add(s);
  for(const item of result.items)annotationCache.set(item.sentence,item);
  document.querySelectorAll('[data-reading-text]').forEach(el=>{if(annotationCache.has(el.dataset.readingText))el.innerHTML=readingHTML(el.dataset.readingText);});
  loaded=true;
 }catch(e){/* Cached annotations are optional; the explicit annotate action can retry. */}
 finally{readingFlight=false;}
 if(loaded)void loadVisibleReadings();
}
async function openDictionary(word,sentence=''){
 const request=++dictionaryRun,context=learningReadContext();
 await run('正在查词…',async()=>{
  const d=await rpc('dictionary',{word,sentence});if(request!==dictionaryRun||!learningReadCurrent(context))return;
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
   const refs=page==='lessons'?[lesson?.id]:page==='immersion'?[story?.id]:[];
   for(const ref of refs.filter(Boolean)){viewedSources.delete(ref);await noteExposure(ref);}
  },b);return;
 }
 if(b.dataset.lookup){await openDictionary(b.dataset.lookup,b.dataset.sentence||'');return;}
 if(b.dataset.dictionaryAdd){await run('正在加入词卡…',async()=>{acceptCardMutation(await rpc('dictionary_add',{word:b.dataset.dictionaryAdd,compact:true}));b.disabled=true;b.textContent='已加入词卡';toast('已加入词卡，相同词条不重复保存。');});return;}
});
