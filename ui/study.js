'use strict';
// Independent page state and serialized writes; grading remains in Python.
const studyLevels=['N5','N4','N3','N2','N1'];
const studySkills={vocabulary:'文字词汇',grammar:'语法',reading:'阅读',listening:'听力'};
const studySource={official:'官方问题集',ai:'AI 模拟题',import:'本地导入',grammar:'语法练习',original:'原创练习'};
let grammarLevel=localStorage.getItem('haru-grammar-level')||'N5',examLevel=localStorage.getItem('haru-exam-level')||'N5';
let grammarData=null,grammarPoint=null,grammarSelectedPointId=null,grammarQuery='',grammarFilter='all',studyCatalog=null,studyAttempt=null,studyQuestion=0,studyPanel='papers',studyMistakes=[],studyError='',studyGeneration=false,studyAssets=[];
try{studyAssets=JSON.parse(localStorage.getItem('haru-study-assets')||'[]');if(!Array.isArray(studyAssets))studyAssets=[];}catch{studyAssets=[];}
let studyQueue=Promise.resolve(),studySaveFailed=false,studyClockFlight=false;
let studyGenerationJob=null,studyGenerationRun=0,studyGenerationCancelling=false;
let studyGenerationRecoveryMessage='',studyGenerationRecoveryCount=0,studyGenerationWaitUntil=0,studyGenerationWaitWake=null;
let studyGenerationStartParams=null;
let studyGenerationMode='full',studyGenerationType='',studyGenerationCount=5;
let studyPageLoadRun=0;
const studyReadRuns={grammarCatalog:0,grammarDetail:0,studyCatalog:0,studyMistakes:0,attempt:0,directory:0};
const studyImages=new Map();
function studyReadStart(key){studyReadRuns[key]=(studyReadRuns[key]||0)+1;return studyReadRuns[key];}
function studyReadCurrent(key,request){return studyReadRuns[key]===request;}
function studyPageCurrent(target,request){return page===target&&studyPageLoadRun===request;}
function studyAcceptCatalog(data){
 studyCatalog=data;
 // A catalog request may finish during an AI call; keep the running job's newer state.
 if(!studyGeneration&&!studyGenerationCancelling){
  if(data.generation)studyGenerationJob=data.generation;
  else if(['active','failed'].includes(studyGenerationJob?.status))studyGenerationJob=null;
 }
 if(!data.blueprint?.types.some(t=>t.id===studyGenerationType))studyGenerationType=data.blueprint?.types[0]?.id||'';
}
function studySummaryHTML(){const s=state.study||{};return `<section class="card study-summary" style="margin-top:24px"><h3>语法与 JLPT 练习</h3><div class="metrics"><div class="metric"><strong>${s.read||0}</strong><p>已阅读语法点</p></div><div class="metric"><strong>${s.mastered||0}</strong><p>最近练习达到80%的语法点</p></div><div class="metric"><strong>${s.due||0}</strong><p>到期语法复习</p></div><div class="metric"><strong>${s.submitted||0}</strong><p>已交卷练习（含语法）</p></div></div><p class="hint">此处独立记录，不改变每日课程阶段；阅读不等于掌握，练习成绩不代表 JLPT 等级认证。</p></section>`;}
function studyButton(label,action,extra='',klass=''){return `<button type="button" class="btn ${klass}" data-study="${action}" ${extra}>${label}</button>`;}
function levelTabs(lv,kind){return `<div class="study-levels" role="group" aria-label="${kind==='grammar'?'语法':'题库'}等级">${studyLevels.map(n=>`<button type="button" class="${lv===n?'active':''}" data-study="level" data-kind="${kind}" data-level="${n}">${n}<small>${{N5:'入门',N4:'基础',N3:'进阶',N2:'中高级',N1:'高级'}[n]}</small></button>`).join('')}</div>`;}
function studyRememberAttempt(a){
 studyAttempt=a;studySaveFailed=false;
 const target=a.paper.source_type==='grammar'?'grammar_library':'jlpt';
 if(target==='grammar_library'){grammarLevel=a.paper.level;localStorage.setItem('haru-grammar-level',grammarLevel);}else{examLevel=a.paper.level;localStorage.setItem('haru-exam-level',examLevel);}
 localStorage.setItem('haru-attempt-'+target,a.id);
 if(a.mode==='timed'&&a.status==='active'){
  const qs=a.paper.questions;const sid=a.paper.sections[a.section_index].id;
  if(qs[studyQuestion]?.section!==sid)studyQuestion=qs.findIndex(q=>q.section===sid);
 }
}
async function loadStudy(target){
 const pageRun=++studyPageLoadRun,level=target==='grammar_library'?grammarLevel:examLevel;
 await run('正在打开学习内容…',async()=>{
  const catalogKey=target==='grammar_library'?'grammarCatalog':'studyCatalog',catalogRun=studyReadStart(catalogKey);
  const data=target==='grammar_library'?await rpc('grammar_catalog',{level}):await rpc('study_catalog',{level});
  if(!studyPageCurrent(target,pageRun)||!studyReadCurrent(catalogKey,catalogRun))return;
  if(target==='grammar_library')grammarData=data;else studyAcceptCatalog(data);
  const attemptRun=studyReadStart('attempt'),id=localStorage.getItem('haru-attempt-'+target);let attempt=null;
  if(id){try{attempt=await rpc('study_attempt',{id});}catch(e){if(!studyPageCurrent(target,pageRun)||!studyReadCurrent('attempt',attemptRun)||!studyReadCurrent(catalogKey,catalogRun))return;localStorage.removeItem('haru-attempt-'+target);studyError=e.message;}}
  if(!studyPageCurrent(target,pageRun)||!studyReadCurrent('attempt',attemptRun)||!studyReadCurrent(catalogKey,catalogRun)||(target==='grammar_library'?grammarLevel:examLevel)!==level)return;
  studyAttempt=null;studyError=studyError||'';
  if(attempt){studyRememberAttempt(attempt);const attemptLevel=attempt.paper.level;if(attemptLevel!==level){const key=target==='grammar_library'?'grammarCatalog':'studyCatalog',runId=studyReadStart(key);const matching=target==='grammar_library'?await rpc('grammar_catalog',{level:attemptLevel}):await rpc('study_catalog',{level:attemptLevel});if(!studyPageCurrent(target,pageRun)||!studyReadCurrent(key,runId))return;if(target==='grammar_library')grammarData=matching;else studyAcceptCatalog(matching);}}
  if(target==='grammar_library'&&!studyAttempt){
   const selected=localStorage.getItem('haru-grammar-point');
   const point=grammarData.items.find(g=>g.id===selected)||grammarData.items[0];
   grammarSelectedPointId=point?.id||null;
   if(point){const detailRun=studyReadStart('grammarDetail'),detail=await rpc('grammar_detail',{id:point.id});if(!studyPageCurrent(target,pageRun)||!studyReadCurrent('grammarDetail',detailRun)||grammarSelectedPointId!==point.id)return;grammarPoint=detail;}
   else grammarPoint=null;
  }
  if(studyPageCurrent(target,pageRun))render();
 });
}
function grammarLibraryView(){
 if(studyAttempt?.paper.source_type==='grammar')return studyAttemptView();
 return title('语法学习','从句子结构到细微语气，按自己的节奏逐级积累。')+levelTabs(grammarLevel,'grammar')+
 `<div class="study-intro"><p>每级 20 个核心语法点 · 四个单元 · 自由浏览</p><small>Haru 教学分级，非官方完整考纲。阅读与练习掌握分别记录。</small>${grammarData?`<p>本级已阅读 ${grammarData.items.filter(g=>g.progress.read_at).length} / 20 · 练习掌握 ${grammarData.items.filter(g=>g.progress.mastered).length} / 20 · 待复习 ${grammarData.items.filter(g=>g.progress.due&&new Date(g.progress.due)<=new Date()).length}</p>`:''}</div>`+
 (grammarData?`<div class="grammar-layout"><section class="card grammar-directory"><label for="grammar-search">查找语法</label><input id="grammar-search" placeholder="句型、中文含义或接续…" value="${esc(grammarQuery)}"><select id="grammar-filter" aria-label="语法学习状态">${[['all','全部语法'],['favorite','我的收藏'],['due','待复习'],['weak','需要加强']].map(([v,t])=>`<option value="${v}" ${grammarFilter===v?'selected':''}>${t}</option>`).join('')}</select><div id="grammar-directory-list">${grammarRows()}</div></section><div class="stack">${grammarDetailHTML()}</div></div>`:empty('正在读取语法目录','内容保存在本地，可离线学习。'));
}
function grammarRows(){
 const now=new Date().toISOString();const query=grammarQuery.trim().toLowerCase();
 const rows=(grammarData?.items||[]).filter(g=>[g.title,g.meaning,g.connection].join(' ').toLowerCase().includes(query)).filter(g=>grammarFilter==='all'||grammarFilter==='favorite'&&g.progress.favorite||grammarFilter==='due'&&g.progress.due&&new Date(g.progress.due)<=new Date()||grammarFilter==='weak'&&g.progress.attempts&&!g.progress.mastered);
 return [...new Set(rows.map(g=>g.unit))].map(unit=>`<h3 class="study-unit">${esc(unit)}</h3>${rows.filter(g=>g.unit===unit).map(g=>`<button type="button" class="grammar-row ${grammarSelectedPointId===g.id?'selected':''}" data-study="point" data-id="${g.id}"><b>${esc(g.title)}</b><small>${esc(g.meaning)}</small><span>${g.progress.favorite?'★ ':''}${g.progress.mastered?'练习掌握':g.progress.read_at?'已阅读':'待学习'}${g.progress.score!==undefined?' · '+g.progress.score+'%':''}</span></button>`).join('')}`).join('')||'<p class="hint">当前条件下没有语法点。</p>';
}
function grammarDetailHTML(){
 const g=grammarPoint;if(!g)return empty('选择一个语法点','从左侧目录开始。');
 return `<article class="card grammar-detail"><div class="card-title"><span class="pill">${g.level} · ${esc(g.unit)}</span>${studyButton(g.progress.favorite?'★ 已收藏':'☆ 收藏','favorite')}</div><h2 lang="ja">${esc(g.title)}</h2><p class="study-meaning">${esc(g.meaning)}</p><div class="callout"><strong>接续方式</strong><p>${esc(g.connection)}</p></div><h3>放进句子里理解</h3>${g.examples.map(x=>`<div class="study-example"><p class="jp" lang="ja">${japanese(x.jp)} ${speak(x.jp)}</p><p class="reading">${esc(x.kana)}</p><p>${esc(x.zh)}</p>${studyButton('用语法解码器分析','decode-example',`data-text="${esc(x.jp)}"`)}</div>`).join('')}<div class="tip-note"><strong>易错点与辨析</strong><p>${esc(g.pitfall)}</p></div><div class="inline-actions">${studyButton('开始 3 题练习','grammar-practice','','primary')}${studyButton(g.progress.read_at?'已标记阅读':'标记已阅读','read')}</div><p class="hint">${g.progress.attempts?'已练习 '+g.progress.attempts+' 次 · 最近正确率 '+g.progress.score+'%':'完成练习后才记录掌握程度。'}${g.progress.due?' · 下次复习 '+new Date(g.progress.due).toLocaleString('zh-CN'):''}</p><h3>同单元继续学习</h3><div class="inline-actions">${g.related_ids.map(id=>{const row=grammarData.items.find(x=>x.id===id);return row?studyButton(esc(row.title),'point',`data-id="${id}"`):'';}).join('')}</div><details class="study-provenance"><summary>内容与参考来源</summary><p>${esc(g.source)} · ${esc(g.version)}</p>${g.references.map(r=>`<button type="button" class="text-link" data-url="${esc(r.url)}">${esc(r.title)}</button>`).join('<br>')}</details></article>`;
}
function jlptView(){
 if(studyAttempt&&studyAttempt.paper.source_type!=='grammar')return studyAttemptView();
 return title('JLPT 真题','官方问题集、个人试卷与同等级模拟练习。')+levelTabs(examLevel,'exam')+
 `<div class="study-tabbar">${[['papers','题库与模拟题'],['history','作答记录'],['mistakes','错题复习'],['import','导入试卷']].map(([v,t])=>studyButton(t,'panel',`data-panel="${v}"`,studyPanel===v?'primary':'')).join('')}</div>`+
 (studyError?`<p class="callout">${esc(studyError)}</p>`:'')+
 (!studyCatalog?empty('正在读取题库','官方原卷可从来源链接打开。'):studyPanel==='history'?studyHistoryHTML():studyPanel==='mistakes'?studyMistakesHTML():studyPanel==='import'?studyImportHTML():studyPapersHTML());
}
function studyPapersHTML(){
 return `<section class="card study-ai"><div><span class="pill">原创模拟 · ${examLevel}</span><h2>按官方题型，练一张新试卷</h2><p class="hint">按题型分段命题，由独立审题子智能体复核；未通过的题目自动重出，最后经全局审查与自动修改，通过后整合保存。AI 原创内容，尚未经教师审校。</p></div><div id="study-generation-form-area">${studyGenerationFormHTML()}</div><div id="study-generation-progress">${studyGenerationProgressHTML()}</div><p class="hint study-model">配置模型：${esc(studyCatalog.config.model)} · ${esc(studyCatalog.config.provider)}</p></section><div class="study-paper-grid">${studyCatalog.papers.map(p=>`<article class="card study-paper"><div class="study-paper-heading"><span class="pill">${esc(studySource[p.source_type])} · ${p.level}</span>${p.source_type==='ai'?studyButton('删除','delete-paper',`data-id="${esc(p.id)}" aria-label="删除试卷：${esc(p.title)}"`,'small study-delete'):''}</div><h2>${esc(p.title)}</h2><p>${p.count} 题 · ${p.sections.length} 个分区</p><p class="hint">${esc(p.notes||'')}</p>${p.model?`<p class="hint study-model">实际模型：${esc(p.model)}</p>`:''}<label>练习范围<select data-paper-scope="${esc(p.id)}"><option value="all">全部题目</option>${Object.entries(studySkills).map(([k,v])=>`<option value="${k}">${v}专项</option>`).join('')}</select></label><div class="inline-actions">${studyButton('开始练习','start',`data-id="${esc(p.id)}" data-mode="practice"`,'primary')}${studyButton('计时作答','start',`data-id="${esc(p.id)}" data-mode="timed"`)}</div>${p.source_url?`<button type="button" class="text-link" data-url="${esc(p.source_url)}">官方来源与使用说明 ↗</button>`:''}</article>`).join('')}</div><p class="hint">2012 / 2018 是问题集出版年份。官方原卷与音频在来源网站打开；应用内保存答题卡与作答记录。正确率不等于 JLPT 官方尺度分数。</p>`;
}
function studyGenerationLocked(){return studyGeneration||studyGenerationCancelling||['active','failed'].includes(studyGenerationJob?.status);}
function studySafeGenerationMessage(value){return String(value||'').replace(/[\r\n\t]+/g,' ').replace(/\bBearer\s+[^\s,;]+/gi,'[已隐藏凭据]').replace(/\bsk-[A-Za-z0-9_-]{12,}\b/g,'[已隐藏凭据]').replace(/\b(api[-_ ]?key|access[-_ ]?token|secret|password)\s*[:=]\s*[^\s,;]+/gi,'$1=[已隐藏]').replace(/https?:\/\/[^\s?]+\?[^\s]+/gi,'[已隐藏请求链接]').slice(0,240);}
function studyGenerationModeLabel(mode){return mode==='full'?'整卷':mode==='compat'?'兼容练习':'专项';}
function studyGenerationFormHTML(){
 const bp=studyCatalog.blueprint,types=bp?.types||[],targeted=studyGenerationMode==='targeted',selected=types.find(t=>t.id===studyGenerationType);
 const disabled=studyGenerationLocked()?'disabled':'';
 return `<form id="study-generate-form"><fieldset class="study-generation-fields" ${disabled}><legend class="sr-only">AI 模拟试卷设置</legend><div class="study-generation-modes" role="group" aria-label="出题方式">${[['full','完整模拟卷',`按官方问题集题型与题量 · ${bp?.count||'—'} 题`],['targeted','题型专项','选择一种细分题型集中练习']].map(([v,t,n])=>`<label class="study-generation-mode ${studyGenerationMode===v?'selected':''}"><input type="radio" name="mode" value="${v}" ${studyGenerationMode===v?'checked':''}><span><strong>${t}</strong><small>${n}</small></span></label>`).join('')}</div>${targeted?`<div class="study-generation-target"><label>专项题型<select name="type_id" required>${Object.entries(studySkills).map(([skill,title])=>`<optgroup label="${title}">${types.filter(t=>t.skill===skill).map(t=>`<option value="${esc(t.id)}" ${t.id===studyGenerationType?'selected':''}>${esc(t.title)}</option>`).join('')}</optgroup>`).join('')}</select></label><label>题数<select name="count">${[5,10].map(n=>`<option value="${n}" ${studyGenerationCount===n?'selected':''}>${n} 题</option>`).join('')}</select></label></div><p class="hint study-type-description">${esc(selected?.requirements||'请选择要练习的题型。')}</p>`:`<div class="study-blueprint-summary">${(bp?.sections||[]).map(s=>`<span>${esc(s.title)} · ${s.count||types.filter(t=>t.section===s.id).reduce((n,t)=>n+t.count,0)} 题</span>`).join('')}</div>`}<div class="study-generate-submit"><button type="submit" class="btn primary" ${!bp?'disabled':''}>${studyGeneration?'正在生成…':`生成 ${examLevel} ${targeted?'专项练习':'完整模拟卷'}`}</button><span class="hint">${targeted?'专项内容同样经过独立审题。':'整卷含多次命题与审题请求，可在生成时继续使用其他页面。'}</span></div></fieldset></form>${bp?`<details class="study-blueprint"><summary>题型与题量依据 · ${esc(bp.title)}</summary><p class="hint">${esc(bp.note)}</p><ul>${types.map(t=>`<li><span>${esc(studySkills[t.skill])} · ${esc(t.title)}</span><b>${t.count} 题</b></li>`).join('')}</ul><button type="button" class="text-link" data-url="${esc(bp.source_url)}">查看官方问题集 ↗</button></details>`:'<p class="hint">暂时无法读取出题蓝图，请重新打开题库。</p>'}`;
}
function studyRecoveryHTML(j){
 if(j?.status==='failed')return '';
 const message=studyGenerationRecoveryMessage||j?.recovery_message||'';
 const counts=[j?.recovery_count?'任务自动恢复 '+j.recovery_count+' 次':'',studyGenerationRecoveryCount?'连接恢复尝试 '+studyGenerationRecoveryCount+' 次':''].filter(Boolean).join(' · ');
 const seconds=Math.max(0,Math.ceil((studyGenerationWaitUntil-Date.now())/1000));
 return message||counts||seconds?`<p class="hint study-generation-recovery" role="status" aria-live="polite">${esc(message||(!counts?'等待后将自动继续。':''))}${message&&counts?'<br>':''}${esc(counts)}${seconds?`<br><span data-study-retry-wait>${seconds} 秒后自动重试</span>`:''}</p>`:'';
}
function studyGenerationProgressHTML(){
 const j=studyGenerationJob;
 if(!j)return studyGeneration||studyGenerationCancelling?`<section class="study-generation-progress"><div role="status"><span class="spinner"></span> ${studyGenerationCancelling?'正在确认取消请求…':studyGenerationRecoveryMessage?'正在自动恢复生成…':'正在建立生成任务…'}</div>${studyRecoveryHTML()}<div class="inline-actions">${studyButton(studyGenerationCancelling?'正在取消…':'取消本次生成','generation-cancel',studyGenerationCancelling?'disabled':'')}</div></section>`:studyRecoveryHTML();
 const complete=j.status==='complete',cancelled=j.status==='cancelled',failed=j.status==='failed',resumeAllowed=j.resume_allowed===true;
 const globalReview=j.global_review,globalApproved=globalReview?.status==='approved',globalChanges=globalReview?.status==='changes_requested';
 const segments=j.segments||[],work=segments.reduce((n,s)=>n+(s.status==='approved'?2:s.status==='drafted'?1:0),0)+(globalApproved?1:0),total=Math.max(1,segments.length*2+2);
 const percent=complete?100:Math.min(99,Math.round(work/total*100));
 const drafted=segments.length&&segments.every(s=>s.status!=='pending'),reviewed=segments.length&&segments.every(s=>s.status==='approved');
 const retrying=!!j.retrying&&['generating','reviewing'].includes(j.phase)&&!complete&&!cancelled&&!failed;
 const phase=j.phase==='global_review'?'全局审查智能体正在审查整份试卷':retrying?(j.phase==='reviewing'?'审题子智能体正在复审':globalChanges?'根据全局审查自动修改题目':'自动重出未通过的题目'):{generating:'命题子智能体正在出题',reviewing:'审题子智能体独立复核',assembling:'正在整合试卷',complete:'试卷已生成并保存'}[j.phase]||'准备生成';
 const retryHint=retrying?`${j.retry_message||'自动重出未通过的题目，完成后继续独立审题。'}${j.retry_question_count?' 本轮重出 '+j.retry_question_count+' 题。':''}`:'';
 const retrySummary=j.retry_count?`累计自动重出 ${j.retry_count} 轮${retrying&&j.current_retry_count?' · 当前段第 '+j.current_retry_count+' 轮':''}`:'';
 const globalLabel=cancelled?'全局审查已取消':{pending:'等待全局审查',reviewing:'正在全局审查',changes_requested:'正在根据全局审查自动修改',approved:'全局审查已通过'}[globalReview?.status]||'等待全局审查';
 const globalHint=globalReview?`<p class="hint study-global-review" role="status" aria-live="polite"><strong>${globalLabel}${globalReview.round?' · 第 '+globalReview.round+' 轮':''}</strong>${j.global_review_message?'<br>'+esc(j.global_review_message):''}${globalChanges&&j.retry_question_count?'<br>本段自动修改 '+j.retry_question_count+' 题':globalChanges&&globalReview.issue_count?'<br>待修正 '+globalReview.issue_count+' 项':''}${j.global_revision_count?'<br>累计全局修改 '+j.global_revision_count+' 轮':''}${globalReview.model?'<br>全局审查模型：'+esc(globalReview.model):''}</p>`:'';
 const heading=studyGenerationCancelling?'正在确认取消，后续命题已停止':cancelled?'本次生成已取消':failed?(resumeAllowed?'生成已暂停，请修正配置后手动继续':'生成已暂停，自动修复已达上限'):!studyGeneration?'可继续上次生成':studyGenerationWaitUntil>Date.now()?'等待后自动继续生成':studyGenerationRecoveryMessage?'正在自动恢复生成':phase;
 const failure=failed?`<div class="callout study-generation-failure" role="alert"><strong>${resumeAllowed?'自动重试已停止':'此任务无法继续'}</strong><p>${esc(studySafeGenerationMessage(studyGenerationRecoveryMessage||j.error||j.recovery_message)||'生成请求连续失败，任务已暂停，已有进度仍保留。')}</p><p>${resumeAllowed?'检查并修正 API 配置后，再点击“修正配置后继续”。系统会先重新检查配置，保留已经审定的分段。':'本任务已达到自动修复上限。取消后可以创建新任务。'}</p></div>`:'';
 const current=j.current_title&&['generating','reviewing'].includes(j.phase)?`第 ${j.current_segment} / ${j.total_segments} 段 · ${j.current_title}`:'';
 return `<section class="study-generation-progress" aria-label="模拟试卷生成进度"><div class="study-generation-status" role="status" aria-live="polite"><div>${studyGeneration&&!cancelled&&!failed?'<span class="spinner" aria-hidden="true"></span>':''}<strong>${complete?'试卷已生成并保存':heading}</strong></div><span class="pill">${esc(j.level)} · ${studyGenerationModeLabel(j.mode)} · ${j.count} 题</span></div><ol class="study-generation-phases" aria-label="生成步骤"><li class="${drafted?'done':!cancelled&&!failed&&j.phase==='generating'?'current':''}">分段命题</li><li class="${reviewed?'done':!cancelled&&!failed&&j.phase==='reviewing'?'current':''}">独立审题</li><li class="${globalApproved?'done':!cancelled&&!failed&&j.phase==='global_review'?'current':''}">全局审查</li><li class="${complete?'done':!cancelled&&!failed&&j.phase==='assembling'?'current':''}">整合保存</li></ol><progress max="100" value="${percent}" aria-label="生成与审查进度">${percent}%</progress><div class="study-generation-metrics"><span>分段审定 ${j.completed_questions||0} / ${j.count} 题 · ${j.completed_segments||0} / ${j.total_segments} 段</span><b>${percent}%</b></div>${!complete&&!cancelled&&!failed&&current?`<p class="hint">${esc(current)}${!studyGeneration?' · 点击继续生成以恢复':''}</p>`:''}${retryHint||retrySummary?`<p class="hint study-generation-retry" role="status" aria-live="polite">${esc(retryHint)}${retryHint&&retrySummary?'<br>':''}${esc(retrySummary)}</p>`:''}${globalHint}${failure}${studyRecoveryHTML(j)}${cancelled?'<p class="hint">已停止此任务，未保存未完成的试卷。</p>':''}<details class="study-generation-segments"><summary>查看各分段进度</summary><ol>${segments.map((s,i)=>`<li class="${s.status==='approved'?'approved':i+1===j.current_segment&&!complete&&!cancelled&&!failed?'current':''}"><span>${i+1}. ${esc(s.title)} <small>· ${s.count} 题${s.retry_count?' · 自动重出 '+s.retry_count+' 轮':''}</small></span><b>${s.status==='approved'?'已通过审题':i+1===j.current_segment&&studyGeneration&&j.phase==='reviewing'?(retrying?'复审中':'审题中'):s.status==='drafted'?'待审题':i+1===j.current_segment&&studyGeneration?(retrying?'自动重出中':'命题中'):'等待命题'}</b></li>`).join('')}</ol></details>${j.model?`<p class="hint study-model">实际模型：${esc(j.model)}</p>`:''}<div class="inline-actions">${!complete&&!cancelled&&!studyGeneration&&!studyGenerationCancelling&&(!failed||resumeAllowed)?studyButton(failed?'修正配置后继续':'继续生成','generation-resume','','primary'):''}${!complete&&!cancelled?studyButton(studyGenerationCancelling?'正在取消…':'取消本次生成','generation-cancel',studyGenerationCancelling?'disabled':''):''}</div></section>`;
}
function studyPaintGeneration(){
 const area=$('#study-generation-progress');if(!area)return;
 const expanded=area.querySelector('.study-generation-segments')?.open,scroll=area.querySelector('.study-generation-segments ol')?.scrollTop;
 area.innerHTML=studyGenerationProgressHTML();
 const details=area.querySelector('.study-generation-segments');if(details&&expanded)details.open=true;
 if(scroll)area.querySelector('.study-generation-segments ol').scrollTop=scroll;
 const fields=$('#study-generate-form fieldset');if(fields)fields.disabled=studyGenerationLocked();
 const submit=$('#study-generate-form button[type="submit"]');if(submit)submit.textContent=studyGeneration?'正在生成…':`生成 ${examLevel} ${studyGenerationMode==='targeted'?'专项练习':'完整模拟卷'}`;
}
function studyGenerationBackoff(attempt){return Math.min(60,2**Math.min(6,Math.max(1,attempt)));}
function studyServerRetryDelay(job){
 const after=Number(job?.retry_after)||0,until=(Number(job?.retry_at)||0)-Date.now()/1000;
 return Math.min(60,Math.max(0,after,until));
}
function studyGenerationRequest(action,params){
 // Native RPC already has a deadline; HTTP preview requests also need a bounded wait.
 return new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(new Error('生成请求暂未响应。')),610000);
  rpc(action,params).then(value=>{clearTimeout(timer);resolve(value);},error=>{clearTimeout(timer);reject(error);});
 });
}
async function studyWaitGeneration(seconds,runId){
 if(runId!==studyGenerationRun)return false;
 const delay=Math.min(60,Math.max(0,seconds))*1000;
 if(!delay)return true;
 studyGenerationWaitUntil=Date.now()+delay;studyPaintGeneration();
 await new Promise(resolve=>{
  const done=()=>{clearTimeout(timer);if(studyGenerationWaitWake===done)studyGenerationWaitWake=null;resolve();};
  const timer=setTimeout(done,delay);studyGenerationWaitWake=done;
 });
 if(runId!==studyGenerationRun)return false;
 studyGenerationWaitUntil=0;studyPaintGeneration();return true;
}
async function studyShowGeneratedPaper(runId){
 const lv=examLevel,job=studyGenerationJob;
 try{
  const data=await studyGenerationRequest('study_catalog',{level:lv});
  if(runId!==studyGenerationRun)return;
  if(lv===examLevel)studyAcceptCatalog(data);
  studyGenerationRecoveryMessage='';
 }catch{
  if(runId!==studyGenerationRun)return;
  studyGenerationRecoveryCount++;studyGenerationRecoveryMessage='试卷已保存，正在自动恢复题库刷新。';studyPaintGeneration();
  setTimeout(()=>{if(runId===studyGenerationRun&&!studyGenerationCancelling)void studyShowGeneratedPaper(runId);},studyGenerationBackoff(studyGenerationRecoveryCount)*1000);
  return;
 }
 if(page==='jlpt'&&!studyAttempt&&studyPanel==='papers')render();
 toast(`已保存 ${job.level} ${job.count} 题${job.mode==='full'?'完整模拟卷':job.mode==='compat'?'练习':'专项练习'}。`);
}
async function studyStartGeneration(params,runId){
 let reconcile=false,attempts=0;
 while(runId===studyGenerationRun&&!studyGenerationCancelling){
  try{
   if(reconcile){
    const data=await studyGenerationRequest('study_catalog',{level:params.level});
    if(runId!==studyGenerationRun)return false;
    if(data.generation){
     if(data.generation.client_request_id!==params.client_request_id){
      // A task from another window or session needs an explicit Continue click.
      studyGenerationJob=data.generation;studyGenerationStartParams=null;
      studyGenerationRecoveryMessage='检测到另一项未完成任务。可先继续或取消该任务，本次请求未覆盖它。';
      return false;
     }
     studyGenerationJob=data.generation;studyGenerationRecoveryMessage='';return true;
    }
   }
   // A stable key also finds this run after an uncertain response, including a completed task.
   const result=await studyGenerationRequest('study_generation_start',params);
   if(runId!==studyGenerationRun)return false;
   studyGenerationJob=result;studyGenerationRecoveryMessage='';return true;
  }catch{
   if(runId!==studyGenerationRun)return false;
   reconcile=true;attempts++;studyGenerationRecoveryCount++;
   studyGenerationRecoveryMessage='正在确认已创建的任务并自动恢复连接，请稍候。';
   if(!await studyWaitGeneration(studyGenerationBackoff(attempts),runId))return false;
  }
 }
 return false;
}
async function studyRunGeneration(params,resumeFailed=false){
 if(studyGeneration||studyGenerationCancelling)return;
 const runId=++studyGenerationRun;
 studyGenerationWaitWake?.();studyGenerationWaitUntil=0;
 studyGeneration=true;studyGenerationRecoveryMessage='';studyGenerationRecoveryCount=0;
 if(params)studyGenerationJob=null;
 studyGenerationStartParams=params?{...params,client_request_id:'haru_'+(globalThis.crypto?.randomUUID?.()||Date.now().toString(36)+'_'+Math.random().toString(36).slice(2))}:null;
 studyPaintGeneration();
 try{
  if(params&&!await studyStartGeneration(studyGenerationStartParams,runId))return;
  if(runId!==studyGenerationRun||!studyGenerationJob)return;
  if(!params&&resumeFailed&&studyGenerationJob.status==='failed'){
   if(studyGenerationJob.resume_allowed!==true)return;
   let resumed;
   try{resumed=await studyGenerationRequest('study_generation_step',{id:studyGenerationJob.id,resume:true});}
   catch{
    if(runId!==studyGenerationRun)return;
    try{resumed=await studyGenerationRequest('study_generation_status',{id:studyGenerationJob.id});}
    catch{if(runId===studyGenerationRun){studyGenerationRecoveryMessage='续跑结果尚未确认。检查连接状态后，可再次点击继续。';studyPaintGeneration();}return;}
   }
   if(runId!==studyGenerationRun)return;
   studyGenerationJob=resumed;studyGenerationRecoveryMessage='';studyPaintGeneration();
   if(resumed.status==='failed')return;
  }
  let attempts=0,delay=studyServerRetryDelay(studyGenerationJob);
  while(runId===studyGenerationRun&&!['complete','cancelled','failed'].includes(studyGenerationJob.status)){
   if(!await studyWaitGeneration(delay,runId))return;
   try{
    const result=await studyGenerationRequest('study_generation_step',{id:studyGenerationJob.id});
    if(runId!==studyGenerationRun)return;
    studyGenerationJob=result;attempts=0;
    studyGenerationRecoveryMessage='';
    delay=Math.max(studyServerRetryDelay(result),result.busy?1.5:0);
   }catch{
    if(runId!==studyGenerationRun)return;
    attempts++;studyGenerationRecoveryCount++;
    studyGenerationRecoveryMessage='请求暂未完成，正在核对已保存的进度并自动恢复连接。';studyPaintGeneration();
    try{
     const result=await studyGenerationRequest('study_generation_status',{id:studyGenerationJob.id});
     if(runId!==studyGenerationRun)return;
     studyGenerationJob=result;
     if(result.status==='failed')studyGenerationRecoveryMessage='';
    }catch{}
    if(runId!==studyGenerationRun)return;
    delay=Math.max(studyGenerationBackoff(attempts),studyServerRetryDelay(studyGenerationJob));
   }
   studyPaintGeneration();
  }
  if(runId===studyGenerationRun&&studyGenerationJob.status==='complete')await studyShowGeneratedPaper(runId);
 }finally{
  if(runId===studyGenerationRun){studyGeneration=false;studyGenerationWaitUntil=0;studyPaintGeneration();}
 }
}
async function studyFinishCancellation(result,runId){
 if(runId!==studyGenerationRun)return;
 studyGenerationJob=result;studyGenerationCancelling=false;studyGenerationWaitUntil=0;
 studyGenerationStartParams=null;studyGenerationRecoveryMessage='';studyPaintGeneration();
 if(result?.status==='complete')await studyShowGeneratedPaper(runId);
}
async function studyCancelGeneration(){
 if(studyGenerationCancelling||(!studyGenerationJob&&!studyGeneration&&!studyGenerationStartParams))return;
 const params=studyGenerationStartParams,runId=++studyGenerationRun;
 // Stop scheduling model calls immediately, even when confirmation cannot reach the server.
 studyGeneration=false;studyGenerationCancelling=true;studyGenerationWaitWake?.();studyGenerationWaitUntil=0;
 studyGenerationRecoveryMessage='已停止后续命题请求，正在确认取消。';studyPaintGeneration();
 let attempts=0;
 while(runId===studyGenerationRun&&studyGenerationCancelling){
  try{
   if(!studyGenerationJob){
    const data=await studyGenerationRequest('study_catalog',{level:params?.level||examLevel});
    if(runId!==studyGenerationRun)return;
    if(data.generation){
     if(params&&data.generation.client_request_id!==params.client_request_id){
      studyGenerationCancelling=false;studyGenerationStartParams=null;
      studyGenerationRecoveryMessage='本次请求已取消，其他生成任务未受影响。';studyPaintGeneration();return;
     }
     studyGenerationJob=data.generation;
    }else if(params){
     // Resolve an uncertain start with the same idempotency key, then cancel its exact job.
     const result=await studyGenerationRequest('study_generation_start',params);
     if(runId!==studyGenerationRun)return;
     studyGenerationJob=result;
    }else{await studyFinishCancellation(null,runId);return;}
   }
   const result=await studyGenerationRequest('study_generation_cancel',{id:studyGenerationJob.id});
   if(runId!==studyGenerationRun)return;
   if(['complete','cancelled'].includes(result.status)){await studyFinishCancellation(result,runId);return;}
  }catch{
   if(runId!==studyGenerationRun)return;
   if(studyGenerationJob){
    try{
     const result=await studyGenerationRequest('study_generation_status',{id:studyGenerationJob.id});
     if(runId!==studyGenerationRun)return;
     if(['complete','cancelled'].includes(result.status)){await studyFinishCancellation(result,runId);return;}
    }catch{}
   }
  }
  if(runId!==studyGenerationRun)return;
  attempts++;studyGenerationRecoveryCount++;
  studyGenerationRecoveryMessage='取消尚未确认，已停止新的命题请求，正在自动重试取消。';
  if(!await studyWaitGeneration(studyGenerationBackoff(attempts),runId))return;
 }
}
function studyHistoryHTML(){return `<section class="card"><h2>${examLevel} 作答记录</h2><div class="history-list">${studyCatalog.history.map(h=>`<button type="button" data-study="resume" data-id="${h.id}"><b>${esc(h.title)}</b><small>${new Date(h.started*1000).toLocaleString('zh-CN')} · ${h.status==='submitted'?h.correct+'/'+h.total+' 正确':'已答 '+h.answered+'/'+h.total} · ${h.mode==='timed'?'计时':'练习'} · ${h.status==='submitted'?'查看解析':'继续作答'}</small></button>`).join('')||'<p class="hint">完成第一组题目后，这里会留下记录。</p>'}</div></section>`;}
function studyMistakesHTML(){return `<section class="card"><h2>${examLevel} 错题复习</h2><p class="hint">同一内容版本以最近一次该题作答为准；答对后移出待复习列表。</p>${studyMistakes.map(m=>`<div class="study-mistake"><b>${esc(m.question.prompt)}</b><p class="hint">${esc(m.title)}</p><p>${esc(m.question.explanation)}</p><div class="inline-actions">${studyButton('查看本次作答','resume',`data-id="${m.attempt}"`)}${studyButton('重练这份错题','retry',`data-id="${m.attempt}" data-wrong="yes"`)}${m.question.grammar_ids.map(id=>studyButton('学习关联语法','linked-grammar',`data-id="${id}"`)).join('')}</div></div>`).join('')||'<p class="hint">暂时没有待复习错题。</p>'}</section>`;}
function studyImportHTML(){return `<section class="card"><h2>导入自己的试卷</h2><p>先导入 PDF／音频／题图，再导入题目 JSON，或直接创建与 PDF 配套的答题卡。原文件保存在本机，不上传到 LLM。</p><div class="inline-actions">${studyButton('选择本地文件','pick-file','','primary')}${studyButton('查看 JSON 格式示例','import-example')}</div><p class="hint">原生 App 支持 PDF、MP3、M4A、WAV、PNG、JPG、JSON。JSON 最大95KB，题图2MB，PDF／音频50MB。每次选择一个文件。</p><div id="study-assets">${studyAssets.map(a=>`<p>${esc(a.name)}<br><code>${esc(a.asset)}</code> ${studyButton('打开','asset',`data-id="${a.asset}"`)}</p>`).join('')}</div><form id="study-import-form"><label>结构化题目 JSON<textarea id="study-import-json" rows="8" maxlength="95000" placeholder="粘贴 JSON；题目答案索引从 0 开始。"></textarea></label><button class="btn primary">校验并导入</button></form><hr><h3>为 PDF 建立答题卡</h3><p class="hint">每行一道题，使用「题型,题号,答案,选项数」，如 grammar,問題1-1,2,4。此处答案使用原卷的 1 起始编号；不会自动猜测扫描件答案。</p><form id="study-sheet-form"><label>试卷名称<input name="title" required maxlength="150"></label><label>来源说明<input name="source" required maxlength="500" placeholder="例如：个人持有教材、出版社及版本"></label><label>原卷 PDF<select name="pdf"><option value="">不绑定 PDF</option>${studyAssets.filter(a=>a.asset.endsWith('.pdf')).map(a=>`<option value="${a.asset}">${esc(a.name)}</option>`).join('')}</select></label><label>练习时长（分钟）<input name="minutes" type="number" min="1" max="240" value="60"></label><label>逐题答案<textarea name="answers" rows="6" required placeholder="grammar,問題1-1,2,4&#10;reading,問題2-1,1,4"></textarea></label><button class="btn primary">创建 ${examLevel} 答题卡</button></form></section>`;}
function studyAttemptView(){
 const a=studyAttempt,p=a.paper,done=a.status==='submitted',q=p.questions[studyQuestion]||p.questions[0];
 const sid=p.sections[a.section_index].id;
 const visible=p.questions.map((x,i)=>[x,i]).filter(([x])=>done||a.mode==='practice'||x.section===sid);
 const feedback=done?a.result.results.find(r=>r.id===q.id):null;
 const resources=(p.resources||[]).filter(r=>done||!['answer','script'].includes(r.kind));
 return title(p.title,`${p.level} · ${studySource[p.source_type]} · ${a.mode==='timed'?'分区计时':'自由练习'}`)+
 `<div class="study-attempt-tools">${studyButton('返回目录','back')}<span class="pill">${done?'已交卷':'已答 '+Object.values(a.answers).filter(v=>v!==null).length+'/'+p.questions.length}</span>${!done&&a.mode==='timed'?'<strong id="study-clock" aria-live="off"></strong>':''}<span id="study-save-status" role="status">${studySaveFailed?'保存失败，请重新打开记录':'已保存在本机'}</span>${studyButton('重新读取已保存作答','reload-attempt')}</div>`+
 (done?`<section class="card study-score"><strong>${a.result.correct}<small> / ${a.result.total} 题</small></strong><div><h2>正确率 ${a.result.percent}%</h2><p>未答 ${a.result.unanswered} 题 · ${a.mode==='timed'?'用时 '+Math.round(Math.min(a.finished-a.started,p.sections.reduce((n,s)=>n+s.seconds,0))/60)+' 分钟':'活跃作答约 '+Math.round(a.elapsed/60)+' 分钟'}</p><p class="hint">${Object.entries(a.result.skills).filter(([,s])=>s.total).map(([k,s])=>studySkills[k]+' '+s.correct+'/'+s.total).join(' · ')}<br>练习结果，不换算官方尺度分数或判定 JLPT 合格。</p><div class="inline-actions">${studyButton('重新练习','retry',`data-id="${a.id}"`)}${studyButton('只练错题','retry',`data-id="${a.id}" data-wrong="yes"`)}</div></div></section>`:'')+
 `<div class="exam-layout"><aside class="card exam-palette"><h3>答题卡</h3><div class="study-section-list">${p.sections.map((s,i)=>`<span class="${q.section===s.id?'active':''}">${esc(s.title)}${a.mode==='timed'?' · '+Math.round(s.seconds/60)+'分钟':''}</span>`).join('')}</div><div class="answer-palette">${visible.map(([x,i])=>`<button type="button" data-study="question" data-index="${i}" class="${i===studyQuestion?'current ':''}${a.answers[x.id]!==undefined&&a.answers[x.id]!==null?'answered ':''}${a.flags.includes(x.id)?'flagged ':''}${done&&!a.result.results[i].correct?'incorrect':''}" aria-label="第${i+1}题${a.flags.includes(x.id)?' 已标记':''}">${i+1}${a.flags.includes(x.id)?'★':''}</button>`).join('')}</div><p class="hint">着色：已答 · ★：待检查</p>${!done?`<div class="stack">${a.mode==='timed'?studyButton('提交当前分区','next-section','','soft'):''}${studyButton('交卷并查看结果','submit','','primary')}</div>`:''}</aside><article class="card exam-question"><div class="card-title"><span class="pill">第 ${studyQuestion+1} 题 · ${esc(q.type_title||studySkills[q.skill])}</span>${!done?studyButton(a.flags.includes(q.id)?'★ 已标记':'☆ 标记待检查','flag'):''}</div>${p.source_type==='official'?`<div class="callout"><strong>对照官方原卷作答</strong><p>定位：${esc(q.locator)}。请在原卷中阅读题干与选项，再选择对应编号。</p></div>`:''}${p.pdf_asset?studyButton('打开原卷 PDF','asset',`data-id="${p.pdf_asset}"`):''}${q.passage?`<div class="study-passage" lang="ja">${done||a.mode==='practice'?japanese(q.passage):esc(q.passage)}</div>`:''}${q.image_asset?`<div class="study-image" data-study-image="${q.image_asset}">${studyImages.has(q.image_asset)?`<img src="${studyImages.get(q.image_asset)}" alt="本题配图">`:studyButton('加载题图','image',`data-id="${q.image_asset}"`)}</div>`:''}${q.audio_asset?studyButton('播放原音频','asset',`data-id="${q.audio_asset}"`):''}${q.audio_text?`<div class="callout">${speak(q.audio_text,true)} <small>模拟听力 · 系统合成语音</small>${done||a.mode==='practice'?`<details><summary>听力原文</summary><p>${esc(q.audio_text)}</p></details>`:''}</div>`:''}${q.audio_asset||q.audio_text?studyButton('停止播放','stop-audio'):''}<h2 class="study-prompt" lang="ja">${esc(q.prompt)}</h2><div class="study-options">${q.options.map((o,i)=>`<label class="option ${a.answers[q.id]===i?'chosen':''} ${done&&q.answer===i?'correct':''}"><input type="radio" name="study-answer" value="${i}" ${a.answers[q.id]===i?'checked':''} ${done?'disabled':''}><span><b>${String.fromCharCode(65+i)}.</b> ${esc(o)}</span></label>`).join('')}</div>${done?`<div class="result-line ${feedback.correct?'':'wrong'}"><strong>${feedback.correct?'✓ 回答正确':feedback.selected===null?'未作答':'需要再看一眼'} · 正确选项 ${String.fromCharCode(65+q.answer)}</strong><p>${esc(q.explanation)}</p>${q.grammar_ids.map(id=>studyButton('学习关联语法','linked-grammar',`data-id="${id}"`)).join('')}</div>`:''}<div class="study-question-nav">${studyButton('上一题','prev-question')}${studyButton('下一题','next-question')}</div>${resources.length?`<details class="study-provenance" ${p.source_type==='official'?'open':''}><summary>原卷与资料 · 官方 JLPT 网站</summary><div class="inline-actions">${resources.map(r=>`<button type="button" class="text-link" data-url="${esc(r.url)}">${esc(r.title)} ↗</button>`).join('')}</div></details>`:''}<p class="hint">${esc(p.source||'')}${p.model?' · 实际模型 '+esc(p.model):''} · 版本 ${esc(p.version)}</p></article></div>`;
}
async function studyWrite(patch={}){
 const id=studyAttempt?.id;
 const operation=async()=>{
  if(!studyAttempt||studyAttempt.id!==id)throw new Error('作答已切换，请重新打开。');
  if(studySaveFailed)throw new Error('上次保存失败，请先重新读取已保存作答。');
  try{
   const a=await rpc('study_save',{id,revision:studyAttempt.revision,...patch});
   if(studyAttempt?.id===id){studyAttempt=a;if($('#study-save-status'))$('#study-save-status').textContent='已保存在本机';}
   return a;
  }catch(e){studySaveFailed=true;if($('#study-save-status'))$('#study-save-status').textContent='保存失败，请重新读取';throw e;}
 };
 const next=studyQueue.then(operation);studyQueue=next.catch(()=>{});return next;
}
async function studyRefreshDirectory(){
 const target=page,level=target==='grammar_library'?grammarLevel:examLevel,pageRun=studyPageLoadRun,directoryRun=studyReadStart('directory');
 if(!['grammar_library','jlpt'].includes(target))return;
 if(page==='grammar_library'){
  const catalogRun=studyReadStart('grammarCatalog'),data=await rpc('grammar_catalog',{level});
  if(!studyPageCurrent(target,pageRun)||!studyReadCurrent('directory',directoryRun)||!studyReadCurrent('grammarCatalog',catalogRun)||grammarLevel!==level)return;
  grammarData=data;
  const selected=grammarSelectedPointId||grammarPoint?.id||localStorage.getItem('haru-grammar-point'),point=data.items.find(g=>g.id===selected)||data.items[0];
  grammarSelectedPointId=point?.id||null;
  if(point){const detailRun=studyReadStart('grammarDetail'),detail=await rpc('grammar_detail',{id:point.id});if(!studyPageCurrent(target,pageRun)||!studyReadCurrent('directory',directoryRun)||!studyReadCurrent('grammarDetail',detailRun)||grammarLevel!==level||grammarSelectedPointId!==point.id)return;grammarPoint=detail;}else grammarPoint=null;
 }else{
  const catalogRun=studyReadStart('studyCatalog'),data=await rpc('study_catalog',{level});
  if(!studyPageCurrent(target,pageRun)||!studyReadCurrent('directory',directoryRun)||!studyReadCurrent('studyCatalog',catalogRun)||examLevel!==level)return;
  studyAcceptCatalog(data);
 }
 if(studyPageCurrent(target,pageRun)&&studyReadCurrent('directory',directoryRun))render();
}
async function studyAction(b){
 const action=b.dataset.study,actionPage=page,actionPageRun=studyPageLoadRun,actionCurrent=()=>page===actionPage&&studyPageLoadRun===actionPageRun;
 if(action==='generation-resume'){void studyRunGeneration(undefined,true);return;}
 if(action==='generation-cancel'){void studyCancelGeneration();return;}
 if(action==='delete-paper'){
  const paper=studyCatalog?.papers.find(p=>p.id===b.dataset.id&&p.source_type==='ai');if(!paper)return;
  $('#modal-content').innerHTML=`<h2>删除这张 AI 模拟试卷？</h2><p class="hint">${esc(paper.title)}</p><p class="hint">删除后会从题库移除。已有作答记录、成绩和错题快照会保留。</p><div class="inline-actions">${studyButton('确认删除','confirm-delete-paper',`data-id="${esc(paper.id)}"`,'study-delete')}${studyButton('保留试卷','close-dialog')}</div>`;
  $('#modal').showModal();return;
 }
 if(action==='question'){studyQuestion=Number(b.dataset.index);render();return;}
 if(['prev-question','next-question'].includes(action)){
  const a=studyAttempt, sid=a.paper.sections[a.section_index].id;
  const visible=a.paper.questions.map((q,i)=>[q,i]).filter(([q])=>a.status==='submitted'||a.mode==='practice'||q.section===sid).map(([,i])=>i);
  const at=visible.indexOf(studyQuestion)+(action==='next-question'?1:-1);studyQuestion=visible[Math.max(0,Math.min(visible.length-1,at))];render();return;
 }
 await run('正在处理学习记录…',async()=>{
  if(action==='level'){
   if(b.dataset.kind==='grammar'){
    studyReadStart('directory');studyReadStart('attempt');
    const level=b.dataset.level;grammarLevel=level;localStorage.setItem('haru-grammar-level',level);grammarData=null;grammarPoint=null;grammarSelectedPointId=null;render();
    const catalogRun=studyReadStart('grammarCatalog'),data=await rpc('grammar_catalog',{level});
    if(!actionCurrent()||!studyReadCurrent('grammarCatalog',catalogRun)||grammarLevel!==level)return;
    grammarData=data;const point=data.items[0];grammarSelectedPointId=point?.id||null;
    if(point){const detailRun=studyReadStart('grammarDetail'),detail=await rpc('grammar_detail',{id:point.id});if(!actionCurrent()||!studyReadCurrent('grammarCatalog',catalogRun)||!studyReadCurrent('grammarDetail',detailRun)||grammarLevel!==level||grammarSelectedPointId!==point.id)return;grammarPoint=detail;}
   }else{
    studyReadStart('directory');studyReadStart('attempt');
    const level=b.dataset.level;examLevel=level;localStorage.setItem('haru-exam-level',level);studyCatalog=null;studyMistakes=[];render();
    const catalogRun=studyReadStart('studyCatalog'),data=await rpc('study_catalog',{level});
    if(!actionCurrent()||!studyReadCurrent('studyCatalog',catalogRun)||examLevel!==level)return;studyAcceptCatalog(data);
    if(studyPanel==='mistakes'){const mistakesRun=studyReadStart('studyMistakes'),mistakes=await rpc('study_mistakes',{level});if(actionCurrent()&&studyReadCurrent('studyMistakes',mistakesRun)&&examLevel===level&&studyPanel==='mistakes')studyMistakes=mistakes;}
   }
  }else if(action==='point'){
   const id=b.dataset.id,level=grammarLevel;grammarSelectedPointId=id;grammarPoint=null;render();
   const detailRun=studyReadStart('grammarDetail'),detail=await rpc('grammar_detail',{id});
   if(!actionCurrent()||!studyReadCurrent('grammarDetail',detailRun)||grammarLevel!==level||grammarSelectedPointId!==id)return;
   grammarPoint=detail;localStorage.setItem('haru-grammar-point',detail.id);
  }
  else if(action==='read'||action==='favorite'){const id=grammarPoint.id;await rpc('grammar_mark',{id,...(action==='favorite'?{favorite:!grammarPoint.progress.favorite}:{})});if(actionCurrent())await studyRefreshDirectory();}
  else if(action==='grammar-practice'){const id=grammarPoint.id,level=grammarLevel,request=studyReadStart('attempt'),attempt=await rpc('grammar_practice',{id});if(!actionCurrent()||!studyReadCurrent('attempt',request)||grammarLevel!==level||grammarPoint?.id!==id)return;studyQuestion=0;studyRememberAttempt(attempt);}
  else if(action==='panel'){
   const panel=b.dataset.panel,level=examLevel;studyPanel=panel;if(panel!=='mistakes')studyMistakes=[];render();
   const catalogRun=studyReadStart('studyCatalog'),data=await rpc('study_catalog',{level});
   if(!actionCurrent()||!studyReadCurrent('studyCatalog',catalogRun)||examLevel!==level||studyPanel!==panel)return;studyAcceptCatalog(data);
   if(panel==='mistakes'){const mistakesRun=studyReadStart('studyMistakes'),mistakes=await rpc('study_mistakes',{level});if(actionCurrent()&&studyReadCurrent('studyMistakes',mistakesRun)&&examLevel===level&&studyPanel===panel)studyMistakes=mistakes;}
  }
  else if(action==='confirm-delete-paper'){
   const level=examLevel;await rpc('study_delete',{id:b.dataset.id});if(!actionCurrent())return;$('#modal').close();
   if(studyGenerationJob?.paper_id===b.dataset.id)studyGenerationJob=null;
   const catalogRun=studyReadStart('studyCatalog'),data=await rpc('study_catalog',{level});if(!actionCurrent()||!studyReadCurrent('studyCatalog',catalogRun)||examLevel!==level)return;studyAcceptCatalog(data);toast('试卷已删除，已有作答记录已保留。');
  }
  else if(action==='start'){const level=examLevel,request=studyReadStart('attempt');studyQuestion=0;const skill=document.querySelector(`[data-paper-scope="${b.dataset.id}"]`).value,attempt=await rpc('study_start',{id:b.dataset.id,mode:b.dataset.mode,skill});if(!actionCurrent()||!studyReadCurrent('attempt',request)||examLevel!==level)return;studyRememberAttempt(attempt);}
  else if(action==='resume'||action==='reload-attempt'){await studyQueue;if(!actionCurrent())return;const request=studyReadStart('attempt'),attempt=await rpc('study_attempt',{id:b.dataset.id||studyAttempt.id});if(!actionCurrent()||!studyReadCurrent('attempt',request))return;studyQuestion=0;studyRememberAttempt(attempt);if(studyAttempt.paper.source_type==='grammar'&&page==='jlpt'){grammarLevel=studyAttempt.paper.level;await navigate('grammar_library');return;}}
  else if(action==='flag'){const flags=studyAttempt.flags.includes(studyAttempt.paper.questions[studyQuestion].id)?studyAttempt.flags.filter(x=>x!==studyAttempt.paper.questions[studyQuestion].id):[...studyAttempt.flags,studyAttempt.paper.questions[studyQuestion].id];await studyWrite({flags});}
  else if(action==='submit'||action==='next-section'){
   const a=studyAttempt;const pending=a.paper.questions.filter(q=>a.answers[q.id]===undefined||a.answers[q.id]===null).length;
   $('#modal-content').innerHTML=`<h2>${action==='submit'?'确认交卷':'提交当前分区'}</h2><p>整份练习尚有 ${pending} 题未答；未答题按错误计。${action==='next-section'?'提交后不能返回修改本分区。':''}</p><div class="inline-actions">${studyButton('确认提交',action==='submit'?'confirm-submit':'confirm-next','','primary')}${studyButton('继续作答','close-dialog')}</div>`;$('#modal').showModal();return;
  }else if(action==='confirm-submit'||action==='confirm-next'){
   $('#modal').close();await studyWrite(action==='confirm-submit'?{finish:true}:{next_section:true});studyRememberAttempt(studyAttempt);await refresh();
  }else if(action==='retry'){
   const request=studyReadStart('attempt'),attempt=await rpc('study_retry',{id:b.dataset.id,wrong_only:b.dataset.wrong==='yes'});if(!actionCurrent()||!studyReadCurrent('attempt',request))return;studyQuestion=0;studyRememberAttempt(attempt);
   if(studyAttempt.paper.source_type==='grammar'&&page==='jlpt'){grammarLevel=studyAttempt.paper.level;await navigate('grammar_library');return;}
  }else if(action==='back'){
   await studyQueue;if(!actionCurrent())return;localStorage.removeItem('haru-attempt-'+page);studyAttempt=null;await studyRefreshDirectory();
  }else if(action==='linked-grammar'){
   await studyQueue;grammarLevel=b.dataset.id.slice(0,2).toUpperCase();localStorage.setItem('haru-grammar-level',grammarLevel);localStorage.setItem('haru-grammar-point',b.dataset.id);localStorage.removeItem('haru-attempt-grammar_library');await navigate('grammar_library');return;
  }else if(action==='decode-example'){drafts.grammar=b.dataset.text;await navigate('grammar');return;}
  else if(action==='pick-file'){
   if(!haruHasNative())throw new Error('文件选择请在 Haru 桌面 App 中使用；浏览器预览可粘贴 JSON。');
   const result=await rpc('study_pick');if(result.cancelled)return;
   if(result.paper){$('#study-import-json').value=JSON.stringify(result.paper,null,2);return;}
   studyAssets.push(result);localStorage.setItem('haru-study-assets',JSON.stringify(studyAssets));
  }else if(action==='asset'){if(!haruHasNative())throw new Error('本地媒体请在 Haru 桌面 App 中打开。');await rpc('study_open_asset',{id:b.dataset.id});return;}
  else if(action==='image'){const d=await rpc('study_image',{id:b.dataset.id});studyImages.set(b.dataset.id,d.url);}
  else if(action==='stop-audio'){if(haruHasNative())await rpc('study_stop_audio');else speechSynthesis.cancel();return;}
  else if(action==='import-example'){
   $('#study-import-json').value=JSON.stringify({title:'N5 个人练习（格式示例）',level:'N5',version:'1',source:'填写出版社／教材／原创来源',sections:[{id:'s1',title:'语法',seconds:600}],questions:[{id:'q1',section:'s1',skill:'grammar',prompt:'私は学生（　）。',options:['です','ます','います','あります'],answer:0,explanation:'名词谓语使用です。',grammar_ids:[],passage:'',audio_text:'',audio_asset:'',image_asset:''}]},null,2);return;
  }else if(action==='close-dialog'){$('#modal').close();return;}
  if(actionCurrent())render();
 },b);
}
document.addEventListener('click',e=>{const b=e.target.closest('[data-study]');if(b)void studyAction(b);});
document.addEventListener('input',e=>{if(e.target.id==='grammar-search'){grammarQuery=e.target.value;$('#grammar-directory-list').innerHTML=grammarRows();}});
document.addEventListener('change',e=>{
 if(e.target.closest('#study-generate-form')){
  if(e.target.name==='mode'){
   studyGenerationMode=e.target.value;$('#study-generation-form-area').innerHTML=studyGenerationFormHTML();
   $('#study-generate-form input[name="mode"]:checked')?.focus();
  }
  if(e.target.name==='type_id'){
   studyGenerationType=e.target.value;
   const description=$('.study-type-description');if(description)description.textContent=studyCatalog.blueprint.types.find(t=>t.id===studyGenerationType)?.requirements||'';
  }
  if(e.target.name==='count')studyGenerationCount=Number(e.target.value);
 }
 if(e.target.id==='grammar-filter'){grammarFilter=e.target.value;$('#grammar-directory-list').innerHTML=grammarRows();}
 if(e.target.name==='study-answer'){
  const a=studyAttempt,q=a.paper.questions[studyQuestion],selected=Number(e.target.value);
  void studyWrite({answers:{[q.id]:selected}}).then(()=>{if(studyAttempt?.id===a.id&&['jlpt','grammar_library'].includes(page))render();}).catch(e=>toast(e.message,true));
 }
});
document.addEventListener('submit',async e=>{
 const f=e.target;if(!['study-generate-form','study-import-form','study-sheet-form'].includes(f.id))return;
 e.preventDefault();const d=new FormData(f);
 if(f.id==='study-generate-form'){
  if(studyGenerationLocked())return;
  studyGenerationJob=null;
  void studyRunGeneration({level:examLevel,mode:studyGenerationMode,...(studyGenerationMode==='targeted'?{type_id:studyGenerationType,count:studyGenerationCount}:{})});
 }else await run('正在校验试卷…',async()=>{
  let paper;
  if(f.id==='study-import-form'){try{paper=JSON.parse($('#study-import-json').value);}catch{throw new Error('JSON 无法解析，请检查标点及括号。');}}
  else{
   const questions=String(d.get('answers')).trim().split(/\n/).map((line,i)=>{
    const fields=line.split(',').map(s=>s.trim());if(fields.length!==4)throw new Error('第'+(i+1)+'行应包含4列。');
    const [skill,locator,answer,opts]=fields;const n=Number(opts);if(!studySkills[skill]||!Number.isInteger(n)||n<2||n>5)throw new Error('第'+(i+1)+'行题型或选项数无效。');
    return {id:'q'+(i+1),section:'s1',skill,prompt:locator,locator,options:Array.from({length:n},(_,j)=>String(j+1)),answer:Number(answer)-1,explanation:'依据用户导入的答案表，请对照原卷核实。',grammar_ids:[]};
   });
   paper={title:d.get('title'),level:examLevel,source:d.get('source'),pdf_asset:d.get('pdf'),version:'1',sections:[{id:'s1',title:'个人试卷',seconds:Number(d.get('minutes'))*60}],questions};
  }
  const result=await rpc('study_import',{paper});examLevel=paper.level;localStorage.setItem('haru-exam-level',examLevel);studyPanel='papers';studyAcceptCatalog(await rpc('study_catalog',{level:examLevel}));render();toast('已导入 '+result.count+' 道题。');
 },f.querySelector('button'));
});
setInterval(()=>{
 const waiting=$('[data-study-retry-wait]');if(waiting){const seconds=Math.max(0,Math.ceil((studyGenerationWaitUntil-Date.now())/1000));waiting.textContent=seconds?`${seconds} 秒后自动重试`:'正在自动继续…';}
 const a=studyAttempt;if(!a||a.status!=='active'||!['jlpt','grammar_library'].includes(page)||document.hidden)return;
 if(a.mode==='timed'){
  const remain=Math.max(0,Math.ceil(a.deadline-Date.now()/1000));const el=$('#study-clock');if(el)el.textContent=`本分区剩余 ${Math.floor(remain/60)}:${String(remain%60).padStart(2,'0')}`;
  if(remain===0&&!studyClockFlight){studyClockFlight=true;studyQueue.then(()=>rpc('study_attempt',{id:a.id})).then(result=>{if(studyAttempt?.id===a.id){studyRememberAttempt(result);render();}}).catch(e=>toast(e.message,true)).finally(()=>{studyClockFlight=false;});}
 }
},1000);
setInterval(()=>{if(studyAttempt?.status==='active'&&!document.hidden&&['jlpt','grammar_library'].includes(page)&&!studySaveFailed)void studyWrite().then(a=>{if(a.status==='submitted')render();}).catch(e=>toast(e.message,true));},20000);
