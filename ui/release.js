'use strict';
let distributionInfo=null, distributionLocked=false;
function distributionSettingsHTML(){
 const d=distributionInfo;
 if(!haruHasNative())return '';
 return `<div class="card" id="distribution-settings"><h3>版本与更新</h3>${d?`<p>Haru ${esc(d.version)} · ${d.distribution==='source'?'源码版':d.distribution==='release'?'正式版':'测试版'}</p><p class="hint">${d.updates_enabled?'更新前会备份学习数据库、AI 配置和朗读设置。':'此构建使用手动更新，可从发布页面下载安装包。'}</p><div class="inline-actions"><button type="button" class="btn" data-release-action="check" ${d.updates_enabled?'':'disabled'}>检查更新</button><button type="button" class="btn" data-release-action="downloads">下载页面</button></div>${d.updates_enabled?`<label class="check-label"><input type="checkbox" id="automatic-updates" ${d.automatic_updates?'checked':''}>自动检查更新</label>`:''}${d.distribution!=='source'?'<hr><h3>从旧版导入</h3><p class="hint">先退出旧版 Haru，再选择原仓库目录。只向空白安装导入已保存的 AI 配置、朗读设置、学习记录和媒体文件；旧 .env 不会导入，原目录保持不变。导入后请重新打开本应用。</p><button type="button" class="btn" data-release-action="import">选择旧版目录</button>':''}`:'<p class="hint">正在读取版本…</p>'}</div>`;
}
async function loadDistributionInfo(){
 if(!haruHasNative())return;
 try{distributionInfo=await rpc('app_info');const card=document.querySelector('#distribution-settings');if(card)card.outerHTML=distributionSettingsHTML();}
 catch(e){const card=document.querySelector('#distribution-settings');if(card)card.innerHTML=`<h3>版本与更新</h3><p class="hint">${esc(e.message)}</p>`;}
}
function maintenanceBusy(){
 return record||busyCount>0||studyGeneration||studyGenerationCancelling||studySaveFailed||studyClockFlight||
  dailyWordLoading||generatingLessons.size>0||pending.size>0||
  (!!studyAttempt&&studyAttempt.status!=='submitted'&&studyAttempt.status!=='completed'&&!studyAttempt.result);
}
window.haruPrepareUpdate=()=>{
 if(distributionLocked||maintenanceBusy())return false;
 distributionLocked=true;
 document.querySelector('.app-shell')?.setAttribute('inert','');
 toast('正在备份学习记录，准备安装更新…');
 return true;
};
window.haruCancelUpdate=()=>{
 distributionLocked=false;
 document.querySelector('.app-shell')?.removeAttribute('inert');
};
document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-release-action]');if(!button)return;
 const action=button.dataset.releaseAction;
 if(action==='import'&&maintenanceBusy()){toast('请先完成学习任务或考试，并停止录音。',true);return;}
 await run(action==='import'?'正在导入旧版数据…':'正在打开…',async()=>{
  if(action==='downloads'){await rpc('open_downloads');return;}
  if(action==='check'){await rpc('check_updates');return;}
  if(action==='import'){
   const result=await rpc('import_legacy');
   if(result.imported){distributionLocked=true;document.querySelector('.app-shell')?.setAttribute('inert','');$('#modal-content').innerHTML='<h2>导入完成</h2><p>原目录已保留。请退出并重新打开此 Haru 应用，继续使用你的学习记录。</p>';$('#modal').showModal();}
  }
 },button);
});
document.addEventListener('change',async event=>{
 if(event.target.id!=='automatic-updates')return;
 const checkbox=event.target;
 try{const result=await rpc('update_preferences',{enabled:checkbox.checked});distributionInfo.automatic_updates=result.automatic_updates;}
 catch(e){checkbox.checked=!checkbox.checked;toast(e.message,true);}
});
