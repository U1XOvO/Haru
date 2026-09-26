'use strict';
const $=s=>document.querySelector(s);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const paths={home:'M3 10 12 3l9 7M5 9v11h5v-6h4v6h5V9',book:'M12 6c-3-3-7-3-10-1v15c3-2 7-2 10 1 3-3 7-3 10-1V5c-3-2-7-2-10 1Zm0 0v15',cards:'m6 3 14 2-2 16L4 19 6 3ZM8 8h8M8 12h6',chat:'M21 11a9 8 0 0 1-9 8H7l-5 3 1-7a8 8 0 0 1 9-12 9 8 0 0 1 9 8ZM8 10h8M8 14h5',grammar:'M4 5h16M8 3v2M6 9c2 6 6 8 9 9M16 5c-1 8-6 13-12 15m12-7 4 8m-6-2h7',chart:'M4 3v18h17M8 16v-4m5 4V7m5 9v-6',globe:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18Z',calendar:'M4 5h16v16H4V5ZM8 3v4m8-4v4M4 10h16M8 14h2m4 0h2m-8 3h2',settings:'M9 3h6l1 4 4 2v6l-4 2-1 4H9l-1-4-4-2V9l4-2 1-4Zm6 9a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',arrow:'M4 12h16m-6-6 6 6-6 6',chevron:'m9 6 6 6-6 6',headphones:'M4 14v-3a8 8 0 0 1 16 0v3M4 12H2v7h5v-7H4Zm16 0h2v7h-5v-7h3Z',sound:'M10 5 5 9H2v6h3l5 4V5Zm4 3a5 5 0 0 1 0 8m3-11a9 9 0 0 1 0 14',spark:'m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3 3-7ZM21 2v4m-2-2h4',leaf:'M20 3C8 2 2 9 5 16s15 2 15-13ZM4 21 15 10',mic:'M9 5a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0V5ZM5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8',check:'m5 12 4 4L19 6',download:'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',stop:'M6 6h12v12H6Z',pause:'M8 5v14m8-14v14',play:'m7 4 14 8-14 8V4Z',refresh:'M3 11a9 9 0 0 1 15-7l3 3M21 2v5h-5M21 13a9 9 0 0 1-15 7l-3-3m0 5v-5h5'};
const icon=n=>`<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[n]||paths.spark}"/></svg>`;
const navItems=[['home','home','今日学习'],['lessons','book','每日课程'],['grammar_library','book','语法学习'],['jlpt','calendar','JLPT 真题'],['cards','cards','即时学习卡'],['grammar','grammar','语法解码器'],['immersion','globe','沉浸日语'],['progress','chart','学习进度']];
let state, page='home', lesson=null, test=null, cardList=[], flipped=false, selectedCard=null, cardSearch='', generatingCard=false, randomCount=5, decoded=null, story=null, record=false, busyCount=0, lastExport=null, catalog=null, checkpoint=null;
let storyPlayback='idle', storyPausePending=false, storyAudioTimer=null, audioRequest=0;
let selectedLessonNo=null, lessonLoading=false, lessonLoadError='', lessonRequest=0, stageRequest=0;
let navigationRun=0,cardLoadRun=0,progressLoadRun=0,refreshRun=0;
let cardQueue=[],cardDetail=null,cardCounts={total:0,due:0},cardNextCursor=null,cardPageBefore=null,cardPageStack=[],cardHistoryRun=0,cardDetailRun=0,cardSearchTimer=null,generatedCards=[];
const generatingLessons=new Set();
let aiConfig=null;
let dailyWord=null, dailyWordLoading=false, dailyWordError='', dailyWordAdded=false;
const drafts={grammar:'わたしは中国人です。',word:'',topic:'街角的咖啡店'};
const pending=new Map();let reqId=0;
window.haruResolve=(id,result)=>{const p=pending.get(id);if(!p)return;clearTimeout(p.timer);pending.delete(id);window.haruGenerationDone?.(id);result.ok?p.resolve(result.data):p.reject(new Error(result.error||'操作未完成'));};
window.haruProgress=(id,event)=>{
 const request=pending.get(id);if(!request)return;
 window.haruGenerationEvent?.(id,event,request);
 if(event.event==='audio'&&request.action==='speak'&&page==='immersion'){
  // Later chunks only fill the native queue; they do not resume paused audio.
  if(event.phase==='chunk'&&storyPlayback==='preparing')setStoryPlayback('playing');
  if(event.phase==='reset')setStoryPlayback('preparing');
 }
};
function rpc(action,params={}){if(distributionLocked)return Promise.reject(new Error('正在准备更新或已完成导入，请重新打开 Haru。'));return new Promise((resolve,reject)=>{const id=++reqId;if(haruHasNative()){const timer=setTimeout(()=>{pending.delete(id);window.haruGenerationDone?.(id);reject(new Error('操作超时，请稍后重试。'));},610000);pending.set(id,{resolve,reject,timer,action,page,nav:navigationRun});haruPostMessage({id,action,params});}else if(location.protocol.startsWith('http')){fetch('/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,params})}).then(r=>r.json()).then(r=>r.ok?resolve(r.data):reject(new Error(r.error))).catch(()=>reject(new Error('本地预览连接失败。')));}else reject(new Error('请通过 start.command（macOS）或 start.cmd（Windows）打开应用。'));});}
function toast(message,error=false){const t=$('#toast');t.textContent=message;t.className='show'+(error?' error':'');clearTimeout(t.timer);t.timer=setTimeout(()=>t.className='',error?7500:3500);}
async function run(label,fn,button){if(button?.disabled)return;busyCount++;$('#busy-label').textContent=label;$('#busy').hidden=false;if(button)button.disabled=true;try{return await fn();}catch(e){toast(e.message,true);}finally{if(button)button.disabled=false;busyCount--;if(!busyCount)$('#busy').hidden=true;}}
async function refresh(){const request=++refreshRun;const result=await rpc('bootstrap',{include_knowledge:page==='progress'});if(request!==refreshRun)return state;state=result;if(!catalog||catalog.stage===state.progression.stage)catalog=state.curriculum;$('#profile-name').textContent=state.profile.name;document.body.classList.toggle('hide-romaji',!state.profile.romaji);return state;}
const btn=(text,action,cls='',extra='')=>`<button class="btn ${cls}" data-action="${action}" ${extra}>${text}</button>`;
const title=(name,sub,extra='')=>`<div class="page-title"><div><div class="eyebrow">YOUR JAPANESE JOURNEY</div><h1>${name}</h1><p>${sub}</p></div>${extra}</div>`;
const source=o=>`<div class="source"><span class="dot"></span>${esc(o.source||'')}${o.model?' · '+esc(o.model):''}<span>学习内容可随时对照官方资料核实</span></div>`;
const speak=(s,small=false)=>`<button class="${small?'btn small':'icon-btn'}" data-speak="${esc(s)}" title="日语朗读" aria-label="日语朗读">${icon('sound')}${small?' 听一听':''}</button>`;
function storyAudioControls(){return `${btn(icon('sound')+' 听完整故事','story-play','small',storyPlayback==='preparing'||storyPausePending?'disabled':'')}${btn(icon(storyPlayback==='paused'?'play':'pause')+(storyPlayback==='paused'?' 继续播放':' 暂停播放'),'story-pause','small soft',(storyPlayback==='playing'||storyPlayback==='paused')&&!storyPausePending?'':'disabled')}`;}
function paintStoryAudioControls(){const controls=$('#story-audio-controls');if(controls)controls.innerHTML=storyAudioControls();}
function setStoryPlayback(next){
 storyPlayback=next;paintStoryAudioControls();clearTimeout(storyAudioTimer);
 if(next!=='playing'||!haruHasNative())return;
 const request=audioRequest;
 storyAudioTimer=setTimeout(async()=>{
  try{const result=await rpc('audio_status');if(request===audioRequest&&storyPlayback==='playing'&&!storyPausePending)setStoryPlayback(result?.state==='playing'?'playing':result?.state==='paused'?'paused':'idle');}
  catch{if(request===audioRequest&&storyPlayback==='playing'&&!storyPausePending)setStoryPlayback('idle');}
 },2000);
}
const ex=x=>`<div class="example"><div><p class="jp" lang="ja">${japanese(x.jp)}</p><p class="reading">${esc(x.kana)} <span class="romaji">· ${esc(x.romaji)}</span></p><p class="translation">${esc(x.zh)}</p></div>${speak(x.jp)}</div>`;
const empty=(name,desc,action='')=>`<div class="empty">${icon('leaf')}<h3>${name}</h3><p>${desc}</p>${action}</div>`;
function nav(){return navItems.map(([id,i,t])=>`<button class="nav-item ${page===id?'active':''}" data-nav="${id}">${icon(i)}${t}${id==='cards'&&state?.stats.due?`<span class="nav-count">${state.stats.due}</span>`:''}</button>`).join('');}
async function navigate(to){if(!navItems.some(n=>n[0]===to)&&to!=='settings')to='home';const request=++navigationRun;page=to;if(to==='settings'){aiConfig=null;speechSettings=null};if(to!=='cards')cardLoadRun++;if(to!=='progress')progressLoadRun++;$('#nav').innerHTML=nav();$('#settings-nav').classList.toggle('active',page==='settings');$('#page-crumb').textContent=navItems.find(n=>n[0]===to)?.[2]||'偏好设置';render();window.scrollTo(0,0);if(to==='settings'){await loadAIConfig();await loadSpeechSettings();await loadDistributionInfo();}if(to==='lessons'&&selectedLessonNo===null&&!lesson&&!checkpoint)await openLesson(state.progression.next_lesson||catalog.courses[0].lesson_no);if(to==='cards')await run('正在打开你的卡片…',loadCards);if(to==='grammar_library'||to==='jlpt')await loadStudy(to);if(to==='progress'){const read=++progressLoadRun;await refresh();if(request===navigationRun&&read===progressLoadRun&&page==='progress')render();}}
function render(){if(!state)return;const f={home:homeView,lessons:lessonsView,cards:cardsView,grammar:grammarView,grammar_library:grammarLibraryView,jlpt:jlptView,progress:progressView,immersion:immersionView,settings:settingsView};$('#main').innerHTML=f[page]();enhanceLearning();syncCourseSelection();window.haruGenerationPaint?.();}
function dailyWordView(){
 const header='<div class="card-title"><h3>今日的一点日语</h3><span>DAILY WORD</span></div>';
 if(dailyWordLoading)return header+'<p class="word-meaning" role="status">AI 正在准备这次的一点日语…</p>';
 if(dailyWordError)return header+`<p class="word-meaning" role="alert">${esc(dailyWordError)}</p><div class="word-foot">${btn('重新生成','retry-daily-word','small')}</div>`;
 if(!dailyWord)return header;
 return header+`<div class="word-kana" lang="ja">${esc(dailyWord.word)}</div><div class="word-romaji" lang="ja">${esc(dailyWord.reading)}</div><div class="word-romaji romaji">${esc(dailyWord.romaji)}</div><div class="word-meaning">${esc(dailyWord.meaning)}</div><div class="word-foot">${speak(dailyWord.word,true)}<button class="text-link" data-action="add-daily-word" ${dailyWordAdded?'disabled':''}>${dailyWordAdded?'已加入词卡':'＋ 加入词卡'}</button>${btn('换一个','retry-daily-word','small')}</div><p class="hint">AI 生成 · ${esc(dailyWord.model)}</p>`;
}
function renderDailyWord(){const card=$('#daily-word');if(card)card.innerHTML=dailyWordView();}
async function loadDailyWord(refresh=false){
 if(dailyWordLoading)return;
 dailyWordLoading=true;dailyWordError='';renderDailyWord();
 try{dailyWord=await rpc('daily_word',{refresh});dailyWordAdded=false;}
 catch(e){dailyWordError=e.message;}
 finally{dailyWordLoading=false;renderDailyWord();}
}
function homeView(){const s=state.stats;const next=s.next_day;const progress=state.progression;const dateText=new Date().toLocaleDateString('zh-CN',{month:'long',day:'numeric',weekday:'long'});return title(`你好，${esc(state.profile.name)} <span style="font-size:26px">☀</span>`,'让日语一点一点，走进你的日常。',`<div class="date-pill">${icon('calendar')}${dateText}</div>`)+`<div class="home-grid"><div><section class="hero"><div class="hero-content"><span class="pill">✦ 你的日语旅程，从这里开始</span><h2>每天一点点，<br>离日本更近一点。</h2><p>不必一下子学会所有。今天，<br>从一句简单的问候开始吧。</p>${btn((progress.next_lesson?'继续第 '+progress.next_lesson+' 课':'继续阶段学习')+' '+icon('arrow'),'start-lesson','rose')}</div><img class="hero-art" src="garden.svg" alt="樱花、富士山与窗边的一杯茶"></section>${stagePanel()}<div class="section-heading"><h2>今天，学点什么 <span>DAILY LESSON</span></h2><button class="text-link" data-nav="lessons">课程一览 ${icon('chevron')}</button></div><div class="lesson-preview">${[['grammar','book','blue','一点语法',progress.next_focus,'理解句子'],['speaking','chat','','开口说日语','跟着例句，勇敢说出第一句','跟读练习'],['listening','headphones','peach','让耳朵熟悉日语','听一听，感受日语的节奏','听力练习']].map(([tab,i,c,t,d,b])=>`<button class="mini-lesson" data-action="start-lesson" data-tab="${tab}"><span class="tile-icon ${c}">${icon(i)}</span><h3>${t}</h3><p>${esc(d)}</p><div class="tile-bottom"><span>${b}</span><b>开始 →</b></div></button>`).join('')}</div><div class="section-heading"><h2>换一种方式，亲近日语 <span>EXPLORE</span></h2></div><div class="tool-grid">${[['cards','cards','','把单词变成记忆','例句、记忆技巧与间隔复习'],['grammar','grammar','blue','拆开一句日语','看懂助词、词序和句子结构'],['immersion','globe','green','走进日语的日常','从一个短故事开始沉浸']].map(([n,i,c,t,d])=>`<button class="tool-card" data-nav="${n}"><span class="tile-icon ${c}">${icon(i)}</span><div><h3>${t}</h3><p>${d}</p></div>${icon('chevron')}</button>`).join('')}</div></div><aside class="right-rail"><section class="card progress-card"><div class="card-title"><h3>小小积累，也有力量</h3>${icon('chart')}</div><div class="progress-ring"><strong>${progress.completed}<i style="font-size:16px;font-style:normal;color:var(--muted)"> / ${progress.total}</i></strong><small>阶段 ${progress.stage} · 已完成</small></div><div class="stats-row"><div><strong>${s.streak}<i> 天</i></strong><small>连续学习</small></div><div><strong>${s.cards}<i> 个</i></strong><small>已收集单词</small></div><div><strong>${s.due}<i> 张</i></strong><small>待复习卡片</small></div></div><div class="week-days">${s.activity.map((d,i)=>`<div class="week-day ${d.count?'done':''} ${i===6?'today':''}">${new Date(d.date+'T12:00:00').toLocaleDateString('zh-CN',{weekday:'narrow'})}<div>${d.count?'✓':i===6?'·':'–'}</div></div>`).join('')}</div></section><section class="card word-card" id="daily-word" aria-live="polite">${dailyWordView()}</section><div class="tip-note"><strong>✧ 给刚开始的你</strong>会认汉字，是你的优势。<br>先听声音，再看文字，让日语有自己的记忆。</div></aside></div>`;}
function stagePanel(){const p=state.progression;const status={learning:'继续课程',assessment_due:'可以开始阶段评估',review_required:'先补强，再评估',reassessment_ready:'补强已完成，可以重新评估'}[p.status];return `<section class="card stage-panel"><div class="card-title"><h3>第 ${p.stage} 阶段 · ${esc(p.name)}</h3><span>${p.completed} / ${p.total} 课</span></div><p class="hint">第 ${p.start}–${p.end} 课 · ${status} · 累计完成 ${p.completed_total} 课</p><div class="bar"><i style="width:${p.completed/p.total*100}%"></i></div><p class="hint">${esc(p.pass_rule)}</p><div class="form-row">${btn(p.next_lesson?'继续第 '+p.next_lesson+' 课':p.status==='review_required'?'打开错题补强':p.status==='reassessment_ready'?'重新阶段评估':'开始阶段评估','start-lesson','soft')}</div></section>`;}
function lessonsView(){return title('每日课程','从五十音出发，把语法、口语和听力放进每一天。')+stagePanel()+`<div class="panel-grid"><div class="stack">${lessonSelection()}<div class="card lesson-body" id="lesson-body">${checkpoint?`<h2>${esc(checkpoint.title)}</h2><p class="hint">从本阶段 ${checkpoint.coverage} 节已学课抽取 ${checkpoint.questions.length} 题。${esc(checkpoint.rule)}</p>${quizHTML(checkpoint,'stage_assessment')}${source(checkpoint)}`:lesson?lessonBody():pendingLesson()}</div></div><aside class="stack" style="align-content:start"><div class="card"><h3>五十音 · 随手听一听</h3><p class="hint">平假名 / 片假名 · 点击朗读<br>下表为基础清音，留空位置不是缺失。</p><div class="kana-grid">${state.kana.flat().map(([h,k,r])=>h?`<button class="kana-cell" data-speak="${h}"><b>${h}</b><span>${k}</span><small class="romaji">${r}</small></button>`:'<div></div>').join('')}</div></div><div class="card history-list"><h3>最近打开的课程</h3>${state.recent.filter(l=>l.source==='AI生成').map(l=>`<button data-action="select-lesson" data-day="${l.lesson_no||l.day}">第 ${l.lesson_no||l.day} 课 · ${esc(l.title)}</button>`).join('')||'<p class="hint">完成第一步后，会在这里留下足迹。</p>'}</div></aside></div>`;}
function syncCourseSelection(){
 const list=$('.course-list'),active=list?.querySelector('[aria-pressed="true"]');
 if(!active)return;
 const box=list.getBoundingClientRect(),item=active.getBoundingClientRect();
 if(item.top<box.top)list.scrollTop-=box.top-item.top;
 else if(item.bottom>box.bottom)list.scrollTop+=item.bottom-box.bottom;
}
function lessonSelection(){
 const selected=catalog.courses.find(c=>c.lesson_no===selectedLessonNo);
 return `<div class="card course-selector"><label class="hint" for="lesson-stage">查看已解锁阶段</label><select id="lesson-stage">${state.stages.map(p=>`<option value="${p.stage}" ${p.stage===catalog.stage?'selected':''}>第 ${p.stage} 阶段 · ${esc(p.name)}</option>`).join('')}</select><p class="hint">按内置大纲选择课程。点击即可查看已生成内容，未生成的课程请先使用 AI 创建。</p><div class="course-list" role="group" aria-label="课程列表">${catalog.courses.map(c=>`<button class="course-choice ${c.lesson_no===selectedLessonNo?'selected':''}" data-action="select-lesson" data-day="${c.lesson_no}" aria-pressed="${c.lesson_no===selectedLessonNo}"><strong>第 ${c.lesson_no} 课 · ${esc(c.title)}</strong><span>${c.completed?'已完成 · ':''}${generatingLessons.has(c.lesson_no)?'正在生成':c.generated?'已生成':'待 AI 生成'}</span></button>`).join('')}</div>${selected?`<div class="form-row course-actions"><strong>第 ${selected.lesson_no} 课 · ${esc(selected.title)}</strong>${btn(icon('spark')+' AI 创建课程','generate-lesson','primary',generatingLessons.has(selectedLessonNo)?'disabled':'')}</div>${selected.generated?`<details><summary class="hint">需要不同的例句或练习？</summary><p class="hint">重建会调用 AI；内容校验不通过时最多重试一次。新版本独立保存，旧课与成绩保留。</p>${btn('重建所选 AI 课','regenerate-lesson','soft',generatingLessons.has(selectedLessonNo)?'disabled':'')}</details>`:''}`:''}</div>`;
}
function pendingLesson(){
 const selected=catalog.courses.find(c=>c.lesson_no===selectedLessonNo);
 if(lessonLoading)return '<div class="empty" role="status">正在读取所选课程…</div>';
 if(lessonLoadError)return empty('课程暂时无法读取',esc(lessonLoadError),btn('重新读取','select-lesson','soft',`data-day="${selectedLessonNo}"`));
 if(!selected)return empty('选择一课，开始学习','点击上方课程列表查看课程。');
 return empty(`第 ${selected.lesson_no} 课 · ${esc(selected.title)}`,generatingLessons.has(selectedLessonNo)?'AI 正在生成这节课，请稍候…':'这节课尚未由 AI 生成。请点击上方“AI 创建课程”，生成后即可开始学习。');
}
function lessonBody(){
 const rich=lesson.design_version>=2;
 return `<span class="pill">${lesson.kind==='remedial'?'错题补强':'第 '+(lesson.lesson_no||lesson.day)+' 课'} · 第 ${lesson.stage||1} 阶段</span><h2 style="font-size:28px;margin-top:12px">${esc(lesson.title)}</h2><p class="section-intro">${esc(lesson.goal)}</p>
 ${rich?`<div class="lesson-roadmap"><strong>这一课，你将能够</strong><ol>${lesson.objectives.map(x=>`<li>${esc(x)}</li>`).join('')}</ol><p class="hint">${lesson.sections.length} 节讲解 · ${lesson.vocabulary.length} 个词语 · ${lesson.examples.length} 个例句 · 情境对话 · ${lesson.production.length} 个表达任务 · ${lesson.questions.length} 道练习<br>时间较少时可分两次学习：先理解与听读，再脱稿表达与答题。</p></div>`:lesson.kind==='lesson'?'<p class="callout">这是已保存的简版课程。可通过上方“重建所选 AI 课”生成丰富版本，旧课与成绩会保留。</p>':''}
 <div class="tabs"><button class="tab active" data-lesson-tab="grammar">语法与例句</button><button class="tab" data-lesson-tab="speaking">口语与听力</button><button class="tab" data-lesson-tab="exercise">课后练习</button></div><div id="lesson-tab-content">${lessonTab('grammar')}</div>${reviewCoverage(lesson)}${source(lesson)}`;
}
function lessonTab(tab){
 if(tab==='grammar')return `<div class="block-title">${icon('book')}${lesson.sections?'先建立框架':'今天的一个知识点'}</div><div class="prose">${esc(lesson.grammar)}</div>
 ${(lesson.sections||[]).map((s,i)=>`<section class="block lesson-section"><h3>${i+1}. ${esc(s.title)}</h3><p class="prose">${esc(s.explanation)}</p></section>`).join('')}
 <div class="callout">给中文母语者的提示 · ${esc(lesson.tip)}</div>
 ${lesson.vocabulary?`<section class="block"><div class="block-title">${icon('cards')}本课词汇 · 先听读，再进入句子</div><div class="lesson-vocabulary">${lesson.vocabulary.map(v=>`<div class="lesson-word"><div class="card-title"><strong lang="ja">${esc(v.word)}</strong>${speak(v.word,true)}</div><div class="reading">${esc(v.reading)} <span class="romaji">· ${esc(v.romaji)}</span></div><p class="translation">${esc(v.meaning)}</p></div>`).join('')}</div></section>`:''}
 <div class="block"><div class="block-title">${icon('chat')}放进句子里，读一读</div>${lesson.examples.map(ex).join('')}</div>${btn('去练口语与听力 '+icon('arrow'),'to-speaking','soft','style="margin-top:20px"')}`;
 if(tab==='speaking')return `<div class="block-title">${icon('mic')}从跟读到自己表达</div><p class="prose">${esc(lesson.speaking)}</p>
 ${lesson.dialogue?`<section class="block lesson-dialogue"><h3>情境对话</h3><p class="hint">${esc(lesson.dialogue.scene)}</p>${lesson.dialogue.lines.map(x=>`<div class="dialogue-turn"><span class="tag">${esc(x.speaker)}</span>${ex(x)}</div>`).join('')}</section>`:lesson.examples.map(ex).join('')}
 <div class="inline-actions">${btn(icon('mic')+' 录下我的跟读','record','','id="record-btn"')}${btn(icon('play')+' 回放录音','play-recording')}</div><p class="hint">录音只在本机保存。回放后对照原音；此版本不对发音打分。<span id="record-status"></span></p>
 <div class="block"><div class="block-title">${icon('headphones')}听读示范 · 先听再核对</div>${speak(lesson.listening.jp,true)}<details style="margin-top:13px"><summary class="hint">听完后，展开原文</summary>${ex(lesson.listening)}</details></div>
 ${lesson.production?`<section class="block"><h3>换个情境，自己说</h3><p class="hint">先脱稿表达，再核对参考。合理表达可能不止一种；这部分用于自查，不自动评分。</p>${lesson.production.map((p,i)=>`<article class="production-task"><h4>任务 ${i+1} · ${esc(p.situation)}</h4><p class="prose">${esc(p.task)}</p><ul class="hint">${p.checklist.map(c=>`<li>${esc(c)}</li>`).join('')}</ul><details><summary>完成后查看一种参考表达</summary>${ex(p.sample)}</details></article>`).join('')}</section>`:''}
 ${btn('完成课后练习 '+icon('arrow'),'to-exercise','primary','style="margin-top:20px"')}`;
 return `${lesson.design_version>=2?'<p class="callout">用学过的知识完成新情境任务。听力测验使用独立材料，先完整听完再选择；提交后查看解析。</p>':''}${quizHTML(lesson,lesson.kind)}`;
}
function quizHTML(d,kind){return `<div class="block-title">${icon('leaf')}${{lesson:'巩固一下，再向前走',quiz:'只考本周学过的内容',stage_assessment:'阶段评估 · 仅首次提交生效',remedial:'错题补强 · 可以重新练习'}[kind]}</div><p class="hint">选择答案后提交，查看解析。${kind==='lesson'?'提交后记录本节课程已完成。':kind==='remedial'?'达到总正确率80%、各受测类别60%后，可开始新的阶段评估；补强不增加课次。':kind==='stage_assessment'?'总正确率至少80%，且各受测类别至少60%，才解锁下一阶段。题目是已学题抽样，不是考试认证。':''}</p><form id="quiz-form" data-id="${d.id}">${d.questions.map((q,i)=>`<div class="quiz-question"><h4>${i+1}. ${esc(q.prompt)}</h4>${q.audio?speak(q.audio,true):''}<div class="options" style="margin-top:10px">${q.options.map((o,j)=>`<label class="option"><input type="radio" name="q${i}" value="${j}" required>${esc(o)}</label>`).join('')}</div><div id="feedback-${i}"></div></div>`).join('')}<button class="btn primary" type="submit" style="margin-top:20px">提交答案 ${icon('check')}</button></form><div id="quiz-result"></div>`;}
function cardHistoryRows(){
 return cardList.map(c=>`<button class="word-row history-card ${selectedCard===c.id?'selected':''}" data-action="open-card" data-id="${esc(c.id)}" aria-label="查看词卡：${esc(c.word)}"><span><b lang="ja">${esc(c.word)}</b><small>${esc(c.meaning)}</small></span><span>${c.ready?'待复习':esc(c.due.slice(5,10)+' '+c.due.slice(11,16))}</span></button>`).join('')||`<p class="hint">${cardCounts.total?'没有找到匹配的词卡。':'还没有词卡。生成后会自动保存在这里。'}</p>`;
}
function cardAnswer(c){return `<div class="reading">${esc(c.reading)} <span class="romaji">· ${esc(c.romaji)}</span></div><div class="meaning">${esc(c.meaning)}</div><p class="jp" style="font-size:20px">${esc(c.example)}</p><p class="translation">${esc(c.translation)}</p><p class="hint">✧ ${esc(c.mnemonic)}</p>`;}
function cardPagination(){return `<div class="inline-actions">${btn('上一页','cards-previous','soft small',cardPageStack.length?'':'disabled')}${btn('下一页','cards-next','soft small',cardNextCursor===null?'disabled':'')}</div>`;}
function applyCardCounts(counts){if(!counts)return;cardCounts=counts;if(state?.stats){state.stats.cards=counts.total;state.stats.due=counts.due;}if($('#nav'))$('#nav').innerHTML=nav();}
function paintCardReview(){if(page==='cards'&&$('#card-review-panel'))$('#card-review-panel').innerHTML=cardReviewHTML();}
function paintCardHistory(){if(page!=='cards')return;if($('#card-history'))$('#card-history').innerHTML=cardHistoryRows();if($('#card-pagination'))$('#card-pagination').innerHTML=cardPagination();if($('#card-history-count'))$('#card-history-count').textContent=cardCounts.total+' 张';}
async function loadCards(){
 const request=++cardLoadRun,navRun=navigationRun,query=cardSearch;const historyRun=++cardHistoryRun;
 selectedCard=null;cardDetail=null;cardDetailRun++;flipped=false;
 const [queue,history]=await Promise.all([rpc('card_queue'),rpc('cards_page',{query})]);
 if(request!==cardLoadRun||navRun!==navigationRun||page!=='cards')return;
 cardQueue=queue.items;applyCardCounts(queue.counts);
 if(historyRun===cardHistoryRun&&query===cardSearch){cardList=history.items;cardNextCursor=history.next_cursor;cardPageBefore=null;cardPageStack=[];}
 render();
}
async function loadCardHistory(before=null,stack=[]){
 const request=++cardHistoryRun,navRun=navigationRun,query=cardSearch;
 const result=await rpc('cards_page',{query,before});
 if(request!==cardHistoryRun||navRun!==navigationRun||page!=='cards'||query!==cardSearch)return;
 cardList=result.items;cardNextCursor=result.next_cursor;cardPageBefore=before;cardPageStack=stack;applyCardCounts(result.counts);paintCardHistory();paintCardReview();
}
async function openCard(identity){
 const request=++cardDetailRun,navRun=navigationRun;
 selectedCard=identity;cardDetail=null;
 const cached=cardQueue.find(c=>c.id===identity)||generatedCards.find(c=>c.id===identity);
 const detail=cached||await rpc('card_detail',{id:identity});
 if(request!==cardDetailRun||navRun!==navigationRun||page!=='cards'||selectedCard!==identity)return;
 selectedCard=identity;cardDetail=detail;paintCardReview();paintCardHistory();
}
function acceptCardMutation(result){
 applyCardCounts(result.counts);if(result.stats)state.stats=result.stats;
 const cards=result.generated||(result.card?[result.card]:[]);
 generatedCards=cards;
 if(cards.length){selectedCard=cards[0].id;cardDetail=cards[0];}
 return cards;
}
async function reviewCard(identity,quality){
 const result=await rpc('review',{id:identity,quality});
 applyCardCounts(result.counts);if(result.stats)state.stats=result.stats;
 cardQueue=cardQueue.filter(c=>c.id!==identity);
 cardList=cardList.map(c=>c.id===identity?{...c,due:result.card.due,ready:result.card.ready}:c);
 generatedCards=generatedCards.map(c=>c.id===identity?result.card:c);
 if(cardDetail?.id===identity)cardDetail=result.card;
 flipped=false;paintCardReview();paintCardHistory();
 if(page==='cards'&&!cardQueue.length&&cardCounts.due){
  const request=++cardLoadRun,navRun=navigationRun;const queue=await rpc('card_queue');
  if(request===cardLoadRun&&navRun===navigationRun&&page==='cards'){cardQueue=queue.items;applyCardCounts(queue.counts);paintCardReview();}
 }
 toast(result.interval?'下次复习：'+result.due.slice(0,10):'10分钟后再见一次。');
}
function cardReviewHTML(){
 const viewed=selectedCard&&cardDetail?.id===selectedCard?cardDetail:null,c=viewed||cardQueue[0];
 return `<div class="card-title"><h3>${viewed?'单词卡详情':'今天的复习'}</h3><span>${cardCounts.due} 张到期 · 共 ${cardCounts.total} 张</span></div>`+
 (c?`${viewed?`<div class="flashcard card-detail"><span class="eyebrow">YOUR WORD COLLECTION</span><div class="word" lang="ja">${esc(c.word)}</div>${cardAnswer(c)}</div><p class="hint">${c.created?'收录于 '+esc(c.created.replace('T',' ')):'历史词卡 · 收录时间未记录'} · ${c.ready?'已到期，可返回复习':'下次复习 '+esc(c.due.replace('T',' '))}</p>${btn('返回到期复习','card-review','soft')}`:`<button class="flashcard" data-action="flip"><span class="eyebrow">${flipped?'THE OTHER SIDE':'TAKE YOUR TIME'}</span><div class="word" lang="ja">${esc(c.word)}</div>${flipped?cardAnswer(c):'<p class="hint">在心里想一想，读音和意思是什么？</p>'}<span class="flip-hint">点击卡片${flipped?'返回正面':'翻看答案'}</span></button>`}<div class="inline-actions">${speak(c.word,true)}${speak(c.example,true)}</div>${!viewed&&flipped?`<div class="review-buttons">${[[1,'还没记住','10分钟后'],[3,'有些困难','按进度安排'],[4,'记住了','按进度安排'],[5,'很轻松','按进度安排']].map(([q,t,h])=>`<button data-action="review" data-quality="${q}" data-id="${c.id}">${t}<br><small>${h}</small></button>`).join('')}</div>`:''}${pronunciationHTML(c.pronunciation)}${source(c)}`:empty(cardCounts.total?'今天的卡片都复习好了':'给单词一个小小的家',cardCounts.total?'可以查看历史单词卡，或随机生成一个新词。':'输入单词、随机生成，或从五张入门卡开始。',!cardCounts.total?btn('加入五张入门卡','seed-cards','soft'):''));
}
function cardsView(){
 return title('即时学习卡','随机遇见新单词，也随时回看已经收好的词卡。')+`<div class="cards-layout"><div class="stack"><div class="card"><form id="card-form" class="form-row"><input id="card-word" aria-label="日语或中文单词" placeholder="输入一个单词，如：ありがとう / 咖啡" value="${esc(drafts.word)}" maxlength="100" required><button class="btn primary" ${generatingCard?'disabled':''}>${icon('spark')} 生成学习卡</button></form><div class="inline-actions"><label for="random-count" class="hint">生成数量</label><select id="random-count" aria-label="随机生成数量" ${generatingCard?'disabled':''}>${[1,5,10].map(n=>`<option value="${n}" ${randomCount===n?'selected':''}>${n} 张</option>`).join('')}</select>${btn(icon('refresh')+' 随机生成新词卡','random-card','soft',generatingCard?'disabled':'')}</div><p class="hint">随机生成会排除已有词条，重复时最多尝试三次。整批成功后保存到历史；生成需要 AI 连接。</p></div>
 ${generatedCards.length?`<section class="card"><h3>本次收录 ${generatedCards.length} 张</h3><div class="inline-actions">${generatedCards.map(c=>btn(esc(c.word),'open-card','soft small',`data-id="${esc(c.id)}"`)).join('')}</div></section>`:''}
 <div class="card" id="card-review-panel">${cardReviewHTML()}</div></div>
 <section class="card cards-history" aria-labelledby="card-history-title"><div class="cards-history-header"><div><h2 id="card-history-title">历史单词卡 <span class="hint" id="card-history-count">${cardCounts.total} 张</span></h2><p class="hint">按收录时间从新到旧 · 点击查看完整内容<br>查看历史不会改变复习进度。</p></div><input id="card-search" aria-label="搜索历史单词卡" placeholder="搜索单词、读音或中文" value="${esc(cardSearch)}" maxlength="100"></div><div class="word-list" id="card-history">${cardHistoryRows()}</div><div id="card-pagination">${cardPagination()}</div></section><div class="tip-note"><strong>先回忆，再翻面</strong>努力想起单词的过程，本身就是学习。不认识也没关系，诚实评分就好。</div></div>`;
}
async function generateCard(word,button){
 if(generatingCard)return;
 const count=randomCount;
 generatingCard=true;
 try{await run(word?'正在制作你的学习卡…':'正在寻找未收录的新单词…',async()=>{
  if(page==='cards')render();
  let message;
  if(word){acceptCardMutation(await rpc('card_create',{word,compact:true}));drafts.word='';message='词卡已收好，相同词条不会重复添加。';}
  else{const result=await rpc('card_random',{count,compact:true});acceptCardMutation(result);cardSearch='';message='已收好 '+result.generated.length+' 张不重复的新词卡。';}
  flipped=false;if(page==='cards'){const navRun=navigationRun;await loadCardHistory();const queue=await rpc('card_queue');if(page==='cards'&&navRun===navigationRun){cardQueue=queue.items;applyCardCounts(queue.counts);}}toast(message);
 },button);}finally{generatingCard=false;if(page==='cards')render();}
}

function grammarView(){return title('语法解码器','一句一句拆开，慢慢读懂日语的逻辑。')+`<div class="panel-grid"><div class="stack"><div class="card"><form id="decode-form"><textarea id="decode-input" placeholder="粘贴一句你想读懂的日语…" maxlength="1200" required>${esc(drafts.grammar)}</textarea><div class="form-row" style="margin-top:12px;justify-content:space-between"><span class="hint">成分分解 · 助词解释 · 中文母语者易错点</span><button class="btn primary">${icon('spark')} 解码这句话</button></div></form></div><div class="card">${decoded?`<p class="jp">${esc(decoded.sentence)}</p><p class="reading">${esc(decoded.reading)} <span class="romaji">· ${esc(decoded.romaji)}</span></p><p class="translation">${esc(decoded.translation)}</p><div class="token-row">${decoded.parts.map(x=>`<div class="token"><b>${esc(x.text)}</b><small>${esc(x.role)}</small></div>`).join('')}</div><p class="prose">${esc(decoded.structure)}</p>${decoded.parts.map(x=>`<div class="breakdown-row"><div>${esc(x.text)}<small class="muted" style="display:block">${esc(x.reading)}</small></div><span>${esc(x.role)}</span><p>${esc(x.explanation)}</p></div>`).join('')}<div class="callout">${esc(decoded.pitfall)}</div><h3 style="margin-top:20px">换个例子，再理解一次</h3>${decoded.examples.map(ex).join('')}${source(decoded)}`:empty('让句子变得透明','输入日语句子，Haru 会解释每一块在做什么，以及和中文表达的不同。')}</div></div><aside class="stack" style="align-content:start"><div class="card"><h3>从这些句子试试</h3><div class="history-list">${['わたしは中国人です。','水をください。','猫が好きです。','これは何ですか。'].map(s=>`<button data-example="${esc(s)}">${s}</button>`).join('')}</div></div><div class="tip-note"><strong>一个小提醒</strong>助词像句子里的路标。同一个词，换了助词，也可能走向不同的意思。</div></aside></div>`;}
function progressView(){const s=state.stats;return title('看见自己的进步','每一次开口、每一次想起，都算数。')+stagePanel()+`<div class="metrics"><div class="metric"><strong>${s.lessons}</strong><p>已完成课程 · 以提交练习为准</p></div><div class="metric"><strong>${s.streak} <small>天</small></strong><p>连续学习 · 课程 / 复习 / 测验</p></div><div class="metric"><strong>${s.attempts}</strong><p>已提交测验 · 同一份仅计一次</p></div></div><div class="panel-grid"><div class="stack"><div class="card"><h3>本周学习，小小验收</h3><p class="section-intro">周一至今，依据实际完成课程和已复习单词出题。分数来自已作答题目，不代表 JLPT 等级。</p><div class="form-row">${btn(icon('spark')+' AI 生成周测','weekly-quiz','primary')}${btn('从已学课程抽题','local-quiz')}</div></div><div class="card" id="weekly-quiz">${test?`<h2>${esc(test.title)}</h2><p class="hint">范围：${esc(test.scope.join('、'))}</p>${quizHTML(test,'quiz')}${source(test)}`:empty('了解你已经会了什么','先完成一节课，再来做本周测验。题目根据真实学习记录生成。')}</div></div><aside class="stack" style="align-content:start"><div class="card"><h3>练习表现</h3><p class="hint">课程、周测与阶段评估保留首次成绩；补强取最近一次成绩。</p>${Object.entries(s.skills).map(([k,v])=>`<div class="skill-row"><div><span>${{grammar:'语法与词汇',kana:'假名辨认',listening:'听力理解'}[k]}</span><span>${v.total?Math.round(v.correct/v.total*100)+'% · '+v.total+'题':'暂无记录'}</span></div><div class="bar"><i style="width:${v.total?v.correct/v.total*100:0}%"></i></div></div>`).join('')}</div><div class="card"><h3>值得再看一眼</h3>${state.mistakes.slice(0,4).map(m=>`<div class="callout"><b>${esc(m.prompt)}</b><br>${esc(m.explanation)}</div>`).join('')||'<p class="hint">暂时没有错题记录。错题会成为下一课的复习线索。</p>'}</div></aside></div>`;}
function immersionView(){return title('沉浸日语','在有情节的故事里，读懂更丰富的日语。')+`<div class="panel-grid"><div class="stack"><div class="card"><form class="form-row" id="immersion-form"><input id="immersion-topic" placeholder="今天想走进什么场景？" value="${esc(drafts.topic)}" maxlength="100" required><button class="btn primary">${icon('spark')} 生成故事</button></form><p class="hint">先读日语，听一遍，再按需展开中文。每次只接触一点新词。</p></div><div class="card">${story?`<div class="immersion-cover"><span class="eyebrow">A LITTLE EVERYDAY STORY</span><h2>${esc(story.title)}</h2><p>读一读 · 听一听 · 想象你就在这里</p></div><div class="inline-actions" id="story-audio-controls">${storyAudioControls()}</div>${story.sentences.map(s=>`<div class="story-line"><div class="form-row" style="justify-content:space-between"><p class="jp" lang="ja">${japanese(s.jp)}</p>${speak(s.jp)}</div><details><summary>读音与中文提示</summary><p class="reading">${esc(s.kana)} <span class="romaji">· ${esc(s.romaji)}</span></p><p class="translation">${esc(s.zh)}</p></details></div>`).join('')}<div class="callout">今天的小任务 · ${esc(story.task)}</div>${reviewCoverage(story)}${source(story)}`:empty('让日语成为风景','一间咖啡店、一只猫、一次散步……告诉 Haru 你感兴趣的主题，创建你的日语故事。')}</div></div><aside class="stack" style="align-content:start"><div class="card"><h3>故事里的小词典</h3>${story?story.words.map(w=>`<div class="word-row"><div><b>${esc(w.word)}</b><br><small>${esc(w.reading)} · ${esc(w.meaning)}</small></div><button class="icon-btn" data-add-word="${esc(w.word)}" title="生成学习卡">＋</button></div>`).join(''):'<p class="hint">生词会出现在这里，点击 + 即可用 AI 生成卡片。</p>'}</div><div class="tip-note"><strong>无需读懂每一个字</strong>先抓住大意，再借助读音和中文提示。让好奇心带着你往前走。</div></aside></div>`;}
function aiSelect(name,value,choices){return `<select name="${name}">${choices.map(([v,label])=>`<option value="${v}" ${v===value?'selected':''}>${label}</option>`).join('')}</select>`;}
function aiProviderView(c,index){
 const number=(name,label,min,max,step='any',hint='自动')=>`<label>${label}<input name="${name}" type="number" value="${c[name]??''}" min="${min}" max="${max}" step="${step}" placeholder="${hint}" ${['timeout','retries'].includes(name)?'required':''}></label>`;
 return `<section class="ai-provider" data-provider-id="${esc(c.id)}" aria-label="${esc(c.name)}">
 <div class="ai-provider-header"><button type="button" class="ai-provider-toggle" data-ai-toggle aria-expanded="false" aria-controls="ai-provider-content-${index}" aria-label="展开 ${esc(c.name)} 的连接设置"><span class="ai-provider-identity"><span class="ai-provider-number">${String(index+1).padStart(2,'0')}</span><span class="ai-provider-name"><strong>${esc(c.name)}</strong><span class="ai-provider-caption">${esc(c.model||'填写连接信息即可开始')}</span></span></span><span class="ai-chevron" aria-hidden="true">⌄</span></button>
 <div class="ai-provider-actions"><label class="ai-choice ai-default"><input type="radio" name="active" value="${esc(c.id)}" ${aiConfig.active===c.id?'checked':''}><span>默认使用</span></label><button type="button" class="ai-remove" data-ai-remove="${esc(c.id)}" aria-label="移除 ${esc(c.name)}" title="移除此配置（保存后生效）" ${aiConfig.providers.length===1?'disabled':''}>移除</button></div></div>
 <div class="ai-provider-content" id="ai-provider-content-${index}" hidden>
 <div class="ai-provider-body"><div class="ai-fields">
 <label>配置名称<input name="name" value="${esc(c.name)}" maxlength="80" placeholder="例如：DeepSeek 日常学习" required></label>
 <label>模型 ID<input name="model" value="${esc(c.model)}" maxlength="200" placeholder="例如：deepseek-chat" spellcheck="false" required></label>
 <label class="ai-field-wide">API 地址<input name="base" type="url" value="${esc(c.base)}" placeholder="https://api.example.com/v1" maxlength="2048" spellcheck="false" required></label>
 <label class="ai-field-wide">API 密钥<input name="key" type="password" value="${esc(c.key||'')}" maxlength="4096" autocomplete="new-password" spellcheck="false" placeholder="${c.key_configured?'已配置，留空即可保留':'填写此服务商的 API 密钥'}" ${c.key_configured?'':'required'}><small class="ai-field-note">密钥仅保存在本机；更换 API 地址时需重新填写。</small></label>
 </div></div>
 <details class="ai-advanced"><summary><span>模型参数<small>思考、输出与连接设置</small></span><span class="ai-chevron" aria-hidden="true">⌄</span></summary><div class="ai-advanced-body">
 <div class="ai-parameter-group"><h4>生成偏好</h4><div class="ai-fields">
 <div class="ai-task-policy"><label class="ai-choice"><input type="checkbox" name="task_reasoning" ${c.task_reasoning!==false?'checked':''}><span>按任务优化思考（推荐）</span></label><p class="hint">词卡、查词、注音关闭思考；课程、故事、拆解、命题使用低强度；JLPT 审题保留高强度。关闭后使用下方统一设置。服务商需支持思考强度设置。</p></div><label>统一思考模式${aiSelect('reasoning',c.reasoning==='auto'?'omit':c.reasoning,[['omit','服务商默认'],['off','关闭思考'],['low','低 · low'],['medium','中 · medium'],['high','高 · high'],['max','最高 · max'],['custom','自定义']])}</label>
 <label class="ai-reasoning-custom ${c.reasoning==='custom'?'':'is-hidden'}">自定义思考强度<input name="reasoning_custom" value="${esc(c.reasoning_custom||'')}" maxlength="80" placeholder="例如：xhigh" pattern="[A-Za-z0-9._-]+" ${c.reasoning==='custom'?'required':''} spellcheck="false"></label>
 ${number('max_tokens','输出 token 上限',1,131072,1,'沿用任务默认')}
 ${number('temperature','温度',0,2,'any','沿用任务默认')}${number('top_p','Top P',0,1,'any','不指定')}
 </div><div class="ai-option-list"><label class="ai-choice"><input type="checkbox" name="omit_temperature" ${c.omit_temperature?'checked':''}><span>不发送温度参数</span></label><label class="ai-choice"><input type="checkbox" name="omit_token_limit" ${c.omit_token_limit?'checked':''}><span>输出上限由服务商决定</span></label></div></div>
 <div class="ai-parameter-group"><h4>连接设置</h4><div class="ai-fields">
 ${number('timeout','请求超时（秒）',10,90)}${number('retries','网络重试次数',0,2,1)}
 </div></div>
 </div></details></div></section>`;
}
function aiConfigView(){
 if(!aiConfig)return '<h3>AI 连接</h3><p class="hint" role="status">正在读取配置…</p>';
 return `<div class="ai-settings-heading"><span class="ai-settings-mark" aria-hidden="true">${icon('spark')}</span><div><h3>AI 连接</h3><p>连接喜欢的模型，让 Haru 陪你学习。</p></div></div>
 <form id="ai-config-form" autocomplete="off"><fieldset class="ai-config-fields">
 <div class="ai-compatibility-note"><strong>仅支持 OpenAI 兼容接口</strong><span>服务商必须提供 HTTPS Chat Completions 接口，并支持 JSON 对象输出。</span></div>
 <details class="ai-defaults"><summary>默认调用参数与覆盖规则</summary><div class="ai-defaults-body">
 <div class="ai-table-wrap"><table><thead><tr><th>功能</th><th>temperature</th><th>max_tokens</th></tr></thead><tbody>
 <tr><td>普通教学、词卡、注音、周测、连接测试</td><td>0.55</td><td>5000</td></tr>
 <tr><td>沉浸故事</td><td>0.55</td><td>16000</td></tr>
 <tr><td>每日课程</td><td>0.55</td><td>24000</td></tr>
 <tr><td>JLPT 命题</td><td>0.35</td><td>8000</td></tr>
 <tr><td>JLPT 分段审查、解析修订</td><td>0.1</td><td>8000</td></tr>
 <tr><td>JLPT 整卷审查</td><td>0.1</td><td>12000</td></tr>
 </tbody></table></div>
 <ul><li>明确填写温度：所有功能统一使用该温度。</li><li>明确填写输出上限：所有功能统一使用该 <code>max_tokens</code>。</li><li>留空：采用表中的任务默认值。</li><li>启用按任务优化时，思考强度按用途选择；关闭后，所有功能统一发送所选的 <code>reasoning_effort</code>。</li><li><code>Top P</code>、不发送温度、输出上限由服务商决定，也会统一应用于所有功能。</li></ul>
 </div></details>
 <div class="ai-list-heading"><div><strong>服务商与模型</strong><span class="ai-count">${aiConfig.providers.length}</span></div><button class="btn small soft" type="button" data-ai-add ${aiConfig.providers.length>=20?'disabled':''}>＋ 添加配置</button></div>
 <p class="ai-settings-description">所有 AI 功能使用默认配置，可为同一服务商添加多个模型。</p>
 <div class="ai-provider-list">${aiConfig.providers.map(aiProviderView).join('')}</div>
 <div class="ai-save-area"><p class="ai-settings-description">支持 HTTPS 的 Chat Completions 接口。测试将先保存，再向默认服务商发送一次请求。</p>
 <div class="ai-save-actions"><button class="ai-refresh" type="button" data-action="refresh-config">${icon('refresh')} 重新读取</button><div><button class="btn soft" type="submit" value="test">保存并测试</button><button class="btn primary" type="submit" value="save">保存配置</button></div></div></div>
 </fieldset><p class="hint ai-save-status" id="ping-result" role="status" aria-live="polite"></p></form>`;
}
function collectAIConfig(form){
 const providers=[...form.querySelectorAll('[data-provider-id]')].map(section=>{
  const row={id:section.dataset.providerId};
  for(const name of ['name','base','key','model','reasoning','reasoning_custom'])row[name]=section.querySelector(`[name="${name}"]`).value;
  for(const name of ['temperature','top_p','max_tokens','timeout','retries']){
   const v=section.querySelector(`[name="${name}"]`).value;row[name]=v===''?null:Number(v);
  }
  for(const name of ['omit_temperature','omit_token_limit','task_reasoning'])row[name]=section.querySelector(`[name="${name}"]`).checked;
  row.key_configured=aiConfig.providers.find(p=>p.id===row.id)?.key_configured||false;
  return row;
 });
 return {providers,active:form.querySelector('[name="active"]:checked')?.value,revision:aiConfig.revision};
}
function setAIProviderExpanded(section,expanded){
 const button=section.querySelector('[data-ai-toggle]');
 section.querySelector('.ai-provider-content').hidden=!expanded;
 button.setAttribute('aria-expanded',String(expanded));
 button.setAttribute('aria-label',`${expanded?'收起':'展开'} ${section.getAttribute('aria-label')} 的连接设置`);
}
document.addEventListener('click',event=>{
 const button=event.target.closest('[data-ai-toggle]');
 if(button)setAIProviderExpanded(button.closest('[data-provider-id]'),button.getAttribute('aria-expanded')!=='true');
});
document.addEventListener('invalid',event=>{
 const section=event.target.closest('[data-provider-id]');
 if(section){setAIProviderExpanded(section,true);event.target.closest('.ai-advanced')?.setAttribute('open','');}
},true);
document.addEventListener('click',event=>{
 const button=event.target.closest('[data-ai-add],[data-ai-remove]');if(!button||button.disabled)return;
 const form=button.closest('form');if(!form||form.querySelector('fieldset').disabled)return;
 Object.assign(aiConfig,collectAIConfig(form));
 if(button.hasAttribute('data-ai-add')){
  aiConfig.providers.push({id:'provider_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2,8),name:'新服务商',base:'https://api.openai.com/v1',key:'',model:'',timeout:60,retries:1,task_reasoning:true,reasoning:'omit',reasoning_custom:''});
 }else{
  aiConfig.providers=aiConfig.providers.filter(p=>p.id!==button.dataset.aiRemove);
  if(!aiConfig.providers.some(p=>p.id===aiConfig.active))aiConfig.active=aiConfig.providers[0].id;
 }
 document.querySelector('#ai-settings').innerHTML=aiConfigView();
});
document.addEventListener('change',event=>{
 if(event.target.name!=='reasoning')return;
 const section=event.target.closest('[data-provider-id]'),custom=section?.querySelector('.ai-reasoning-custom');
 if(!custom)return;
 const enabled=event.target.value==='custom';custom.classList.toggle('is-hidden',!enabled);
 custom.querySelector('input').required=enabled;
});
async function loadAIConfig(){
 const target=$('#ai-settings');
 await run('正在读取 AI 配置…',async()=>{
  try{const c=await rpc('config_get');if(target?.isConnected){aiConfig=c;target.innerHTML=aiConfigView();}}
  catch(e){if(target?.isConnected)target.innerHTML='<h3>AI 连接</h3><p class="hint" role="alert">'+esc(e.message)+'</p>'+btn('重新读取','refresh-config');throw e;}
 });
}
async function saveAIConfig(form,testConnection){
 const fields=form.querySelector('fieldset');if(fields.disabled)return;
 const params=collectAIConfig(form),result=form.querySelector('#ping-result');
 fields.disabled=true;result.textContent='正在保存配置…';let saved=false;
 await run(testConnection?'正在保存并测试 AI 连接…':'正在保存 AI 配置…',async()=>{
  try{
   const c=await rpc('config_save',params);saved=true;aiConfig=c;
   form.querySelectorAll('[name="key"]').forEach(input=>{input.value='';input.required=false;});
   result.textContent='已保存，后续请求使用默认服务商。';
   if(testConnection){result.textContent+=' 正在测试连接…';const r=await rpc('ping');result.textContent='配置已保存，连接成功 · 实际返回模型：'+r.model;}
  }catch(e){result.textContent=(saved?'配置已保存；测试连接失败：':'配置未保存：')+e.message;throw e;}
  finally{
   fields.disabled=false;params.providers.forEach(p=>p.key='');
   if(saved&&form.isConnected){const message=result.textContent;document.querySelector('#ai-settings').innerHTML=aiConfigView();document.querySelector('#ping-result').textContent=message;}
  }
 });
}
function settingsView(){const p=state.profile;return title('学习偏好','找到舒服的节奏，让学习自然发生。')+`<div class="panel-grid"><div class="stack"><div class="card"><form id="profile-form"><div class="field-grid"><label>怎么称呼你<input name="name" value="${esc(p.name)}" maxlength="30" required></label><label>每天学习时长（分钟）<input name="minutes" type="number" value="${p.minutes}" min="5" max="90" required></label><label>学习目标<select name="goal">${['日常交流','旅行日语','兴趣与文化','打好基础，再准备JLPT'].map(g=>`<option ${p.goal===g?'selected':''}>${g}</option>`).join('')}</select></label></div><label class="check-label" style="margin:20px 0"><input type="checkbox" name="romaji" ${p.romaji?'checked':''}>显示辅助罗马音（熟悉假名后可以关闭）</label>
<button class="btn primary">保存偏好 ${icon('check')}</button></form></div><div class="card" id="ai-settings">${aiConfigView()}</div><div class="card" id="speech-settings">${speechSettingsView()}</div>${distributionSettingsHTML()}</div><aside class="stack" style="align-content:start"><div class="card"><h3>你的学习记录</h3><p class="hint">课程、卡片、测验及历史对话记录保存在本机 SQLite 数据库。导出包含个人学习内容，请妥善保存。</p>${btn(icon('download')+' 导出学习档案','export-json','soft','style="margin-top:15px"')}</div><div class="tip-note"><strong>关于隐私与声音</strong>点击 AI 功能后，相关输入、近期学习摘要与错题会发送到你配置的 API 服务。<br>朗读可选择 Edge TTS 或 Gemini TTS；未缓存的文字会发送到所选语音服务。Gemini 失败时会尝试 Edge TTS；两个服务都需要网络，已缓存音频可离线播放。跟读录音仅保留在本机。</div></aside></div>`;}
async function openLesson(day,generate=false,tab='grammar',regenerate=false){
 if(generate&&generatingLessons.has(day))return;
 const request=++lessonRequest;
 const same=selectedLessonNo===day;
 selectedLessonNo=day;checkpoint=null;lessonLoadError='';
 if(!same||!generate)lesson=null;
 lessonLoading=!generate;
 if(generate)generatingLessons.add(day);
 const current=()=>selectedLessonNo===day&&(generate||request===lessonRequest);
 await navigate('lessons');
 await run(generate?'Haru 正在为你准备课程…':'正在读取课程…',async()=>{
  try{
   const courseCatalog=await rpc('curriculum',{stage:state.stages.find(p=>p.start<=day&&p.end>=day)?.stage||catalog.stage});
   if(current()){catalog=courseCatalog;if(page==='lessons')render();}
   const d=await rpc('lesson',{day,source:'ai',cached_only:!generate,regenerate});
   if(current()){if(generate)++lessonRequest;lesson=d;lessonLoading=false;}
   if(generate){await refresh();if(current())catalog=await rpc('curriculum',{stage:courseCatalog.stage});}
   if(current()&&page==='lessons'){render();if(lesson)setLessonTab(tab==='listening'?'speaking':tab);}
  }catch(e){
   if(current()){lessonLoading=false;if(!generate)lessonLoadError=e.message;}
   throw e;
  }finally{
   if(generate)generatingLessons.delete(day);
   if(current())lessonLoading=false;
   if(page==='lessons'){
    // Refresh selection/status without throwing away answers in an open lesson.
    const selector=$('.course-selector');if(selector){selector.outerHTML=lessonSelection();syncCourseSelection();}
    if(!lesson&&!checkpoint)$('#lesson-body').innerHTML=pendingLesson();
   }
  }
 });
}
async function continueLearning(tab='grammar'){const request=navigationRun;await refresh();if(request!==navigationRun)return;const p=state.progression;if(p.next_lesson)return openLesson(p.next_lesson,false,tab);return openCheckpoint(p.status==='review_required');}
async function openCheckpoint(remedial=false){const request=navigationRun;selectedLessonNo=null;++lessonRequest;lessonLoading=false;await run(remedial?'正在整理错题补强…':'正在准备阶段评估…',async()=>{const d=await rpc(remedial?'remedial':'stage_assessment');if(request!==navigationRun)return;if(remedial){lesson=d;checkpoint=null;}else{checkpoint=d;lesson=null;}await refresh();if(request!==navigationRun)return;catalog=state.curriculum;await navigate('lessons');});}
function setLessonTab(tab){document.querySelectorAll('[data-lesson-tab]').forEach(b=>b.classList.toggle('active',b.dataset.lessonTab===tab));if($('#lesson-tab-content')){$('#lesson-tab-content').innerHTML=lessonTab(tab);if($('#lesson-tab-content [data-reading-text]'))void noteExposure(lesson?.id);void loadVisibleReadings();}}
async function exportData(type){await run('正在导出…',async()=>{lastExport=await rpc('export',{type});$('#modal-content').innerHTML=`<h2>已经导出学习档案</h2><p class="hint">学习档案含课程、词卡与对话记录。</p><p class="prose" style="margin:18px 0">${esc(lastExport.path)}</p><div class="form-row">${btn('打开文件位置','reveal-export','primary')}${btn('完成','close-modal')}</div>`;$('#modal').showModal();});}
async function handleAction(a,b){switch(a){case 'retry-daily-word':await loadDailyWord(true);break;case 'add-daily-word':if(!dailyWord||dailyWordAdded)break;await run('正在收好这张词卡…',async()=>{acceptCardMutation(await rpc('daily_word_add',{id:dailyWord.id,compact:true}));dailyWordAdded=true;if(page==='home')render();toast('已加入词卡，相同词条不会重复添加。');},b);break;case 'random-card':await generateCard(null,b);break;case 'open-card':await run('正在读取词卡…',()=>openCard(b.dataset.id),b);break;case 'card-review':selectedCard=null;cardDetail=null;flipped=false;paintCardReview();paintCardHistory();break;case 'cards-next':if(cardNextCursor!==null)await run('正在读取下一页…',()=>loadCardHistory(cardNextCursor,[...cardPageStack,cardPageBefore]),b);break;case 'cards-previous':if(cardPageStack.length)await run('正在读取上一页…',()=>loadCardHistory(cardPageStack.at(-1),cardPageStack.slice(0,-1)),b);break;case 'start-lesson':await continueLearning(b?.dataset.tab||'grammar');break;case 'select-lesson':await openLesson(Number(b.dataset.day));break;case 'regenerate-lesson':await openLesson(selectedLessonNo,true,'grammar',true);break;case 'generate-lesson':await openLesson(selectedLessonNo,true);break;case 'to-speaking':setLessonTab('speaking');break;case 'to-exercise':setLessonTab('exercise');break;case 'seed-cards':await run('正在收好入门卡…',async()=>{acceptCardMutation(await rpc('card_seed',{compact:true}));if(page==='cards')await loadCards();toast('五张入门卡已加入，相同词条不会重复添加。');},b);break;case 'flip':flipped=!flipped;paintCardReview();break;case 'review':await run('正在安排下次复习…',()=>reviewCard(b.dataset.id,Number(b.dataset.quality)),b);break;case 'weekly-quiz':case 'local-quiz':await run('正在根据本周记录出题…',async()=>{test=await rpc('quiz',{source:a==='local-quiz'?'builtin':'ai'});if(page==='progress')render();},b);break;case 'export-json':await exportData('json');break;case 'settings':await navigate('settings');break;case 'refresh-config':await loadAIConfig();break;case 'record':if(!haruHasNative()){toast('跟读录音请在 Haru 桌面 App 中使用。');break;}await run(record?'正在保存录音…':'正在准备麦克风…',async()=>{await rpc(record?'record_stop':'record_start');record=!record;if($('#record-btn')){$('#record-btn').innerHTML=icon(record?'stop':'mic')+(record?' 停止并保存':' 录下我的跟读');$('#record-btn').classList.toggle('recording',record);}toast(record?'正在录音，点击停止保存（最长60秒）。':'录音已保存在本机，可以回放。');},b);break;case 'play-recording':await run('准备回放…',()=>rpc('record_play'),b);break;case 'story-play':if(story)await speakText(story.sentences.map(s=>s.jp).join(''));break;case 'story-pause':if(storyPausePending)break;if(!haruHasNative()){toast('日语朗读请在 Haru 桌面 App 中使用。',true);break;}storyPausePending=true;paintStoryAudioControls();try{const result=await rpc('audio_toggle_pause');setStoryPlayback(result?.state==='paused'?'paused':result?.state==='playing'?'playing':'idle');if(storyPlayback==='idle')toast('朗读已结束，请重新播放。');}catch(e){toast(e.message,true);}finally{storyPausePending=false;paintStoryAudioControls();}break;case 'reveal-export':if(lastExport)await rpc('reveal',{path:lastExport.path}).catch(e=>toast(e.message,true));break;case 'close-modal':$('#modal').close();break;}}
document.addEventListener('click',async e=>{const b=e.target.closest('button,a');if(!b)return;if(b.dataset.nav){e.preventDefault();await navigate(b.dataset.nav);return;}if(b.matches('.brand')){e.preventDefault();await navigate('home');return;}if(b.dataset.speak){await speakText(b.dataset.speak);return;}if(b.dataset.lessonTab){setLessonTab(b.dataset.lessonTab);return;}if(b.dataset.example){drafts.grammar=b.dataset.example;$('#decode-input').value=drafts.grammar;return;}if(b.dataset.addWord){const word=b.dataset.addWord;await run('正在为生词制作学习卡…',async()=>{acceptCardMutation(await rpc('card_create',{word,compact:true}));toast('学习卡已加入词卡盒。');},b);return;}if(b.dataset.url){if(haruHasNative())await rpc('open_url',{url:b.dataset.url}).catch(e=>toast(e.message,true));else window.open(b.dataset.url,'_blank','noopener');return;}if(b.dataset.action)await handleAction(b.dataset.action,b);});
document.addEventListener('change',async e=>{if(e.target.id==='lesson-stage'){const request=++stageRequest,nav=navigationRun;const stage=Number(e.target.value);await run('正在读取阶段课程…',async()=>{const next=await rpc('curriculum',{stage});if(request!==stageRequest||nav!==navigationRun||page!=='lessons')return;catalog=next;await openLesson(next.courses[0].lesson_no);});}});
document.addEventListener('change',e=>{if(e.target.id==='random-count')randomCount=Number(e.target.value);});
document.addEventListener('input',e=>{if(e.target.id==='card-search'){cardSearch=e.target.value;cardHistoryRun++;clearTimeout(cardSearchTimer);const navRun=navigationRun;cardSearchTimer=setTimeout(()=>{if(page==='cards'&&navRun===navigationRun)void loadCardHistory().catch(e=>toast(e.message,true));},200);return;}const keys={'decode-input':'grammar','card-word':'word','immersion-topic':'topic'};if(keys[e.target.id])drafts[keys[e.target.id]]=e.target.value;});
document.addEventListener('submit',async e=>{e.preventDefault();const f=e.target;if(f.id==='ai-config-form'){await saveAIConfig(f,e.submitter?.value==='test');return;}const b=f.querySelector('button[type="submit"],button:not([type])');if(f.id==='profile-form'){const d=new FormData(f);await run('正在保存偏好…',async()=>{await rpc('profile',{name:d.get('name'),minutes:Number(d.get('minutes')),goal:d.get('goal'),romaji:d.has('romaji')});await refresh();toast('已保存你的学习节奏。');},b);}if(f.id==='card-form'){await generateCard($('#card-word').value,b);}if(f.id==='decode-form'){const value=$('#decode-input').value;await run('正在一点点拆解句子…',async()=>{decoded=await rpc('decode',{text:value});if(page==='grammar')render();},b);}if(f.id==='immersion-form'){const topic=$('#immersion-topic').value;await run('正在写一个属于你的日语故事…',async()=>{story=await rpc('immersion',{topic});if(page==='immersion')render();},b);}if(f.id==='quiz-form'){const count=f.querySelectorAll('.quiz-question').length;const form=new FormData(f);const answers=Array.from({length:count},(_,i)=>form.has('q'+i)?Number(form.get('q'+i)):null);if(answers.includes(null)){toast('请先完成所有题目。',true);return;}await run('正在核对答案…',async()=>{const r=await rpc('grade',{id:f.dataset.id,answers});await refresh();if(f.isConnected){r.results.forEach((x,i)=>{const t=$('#feedback-'+i);if(t)t.innerHTML=`<div class="result-line ${x.correct?'':'wrong'}">${x.correct?'✓ 回答正确':'再看一眼 · 正确选项 '+String.fromCharCode(65+x.answer)} · ${esc(x.explanation)}</div>`;});$('#quiz-result').innerHTML=`<div class="score-banner"><strong>${r.score}<small> 分</small></strong><div><h3>${r.kind==='stage_assessment'?(r.passed?'阶段通过，下一阶段已解锁':'先补强，再重新评估'):r.kind==='remedial'?(r.passed?'补强完成，可以重新评估':'继续补强不熟悉的题目'):r.score>=80?'今天又前进了一步':'把不熟悉的地方，再看一遍'}</h3><p>${r.recorded?'已保存本次结果；错题会用于后续教学。':r.kind==='stage_assessment'||r.kind==='remedial'?'此份结果已记录，显示已保存成绩。':'本次为重做反馈，首次提交记录保持不变。'}</p>${r.skills?`<p>${Object.entries(r.skills).map(([k,v])=>({grammar:'语法与词汇',kana:'假名',listening:'听力'}[k])+': '+v.correct+'/'+v.total).join(' · ')}</p>`:''}${btn('继续学习 '+icon('arrow'),'start-lesson','soft')}</div></div>`;f.querySelectorAll('input,button').forEach(el=>el.disabled=true);}document.querySelectorAll('.stage-panel').forEach(el=>el.outerHTML=stagePanel());$('#nav').innerHTML=nav();},b);}});
async function speakText(text){
 const request=++audioRequest;const track=page==='immersion';setStoryPlayback(track?'preparing':'idle');
 if(!haruHasNative()){setStoryPlayback('idle');toast('日语朗读请在 Haru 桌面 App 中使用。',true);return;}
 const speed=state?.profile?.speech_rate??1.0;
 try{
  const result=await rpc('speak',{text,rate:0.42*speed});
  if(request===audioRequest){
   if(!track||!result?.engine)setStoryPlayback('idle');
   else if(storyPlayback==='preparing')setStoryPlayback('playing');
  }
  if(result?.fallback)toast('Gemini 朗读失败（'+result.fallback+'），已使用 Edge TTS。');
  return result;
 }catch(e){if(request===audioRequest)setStoryPlayback('idle');toast(e.message,true);}
}
window.haruRecordingStopped=()=>{record=false;if($('#record-btn')){$('#record-btn').innerHTML=icon('mic')+' 录下我的跟读';$('#record-btn').classList.remove('recording');}toast('录音已保存（最长60秒）。');};
$('#settings-nav').innerHTML=icon('settings')+'偏好设置';
(async()=>{try{await refresh();await navigate('home');if(!window.haruSmokeMode)void loadDailyWord();}catch(e){$('#main').innerHTML=empty('暂时没有打开学习空间',esc(e.message),'<p class="hint">请通过 start.command（macOS）或 start.cmd（Windows）启动。</p>');}})();
