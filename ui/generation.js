'use strict';
// Previews are ephemeral. Only the final validated RPC result enters learning state.
const generationPreviews=new Map();
const generationLabels={lesson:'课程',daily_word:'今日词',card_create:'词卡',card_random:'词卡',decode:'句子拆解',immersion:'故事',annotate:'逐词注音',dictionary:'词典',quiz:'周测'};
function generationText(value){
 if(typeof value==='string')return value;
 if(Array.isArray(value))return value.map(generationText).filter(Boolean).join('\n\n');
 if(value&&typeof value==='object')return Object.values(value).map(generationText).filter(Boolean).join('\n');
 return '';
}
window.haruGenerationEvent=(id,event,request)=>{
 if(!generationLabels[request.action]||event.event!=='generation')return;
 generationPreviews.set(id,{action:request.action,page:request.page,nav:request.nav,preview:event.preview||{}});
 window.haruGenerationPaint();
};
window.haruGenerationDone=id=>{
 if(generationPreviews.delete(id))window.haruGenerationPaint();
};
window.haruGenerationPaint=()=>{
 const old=document.querySelector('#generation-preview');
 if(old)old.remove();
 const entries=[...generationPreviews.values()].filter(p=>p.page===page&&p.nav===navigationRun);
 if(!entries.length)return;
 const p=entries.at(-1),text=generationText(p.preview);
 const panel=document.createElement('section');panel.id='generation-preview';panel.className='card generation-preview';
 const label=document.createElement('p');label.className='hint';label.setAttribute('role','status');
 label.textContent=generationLabels[p.action]+'正在生成 · 以下为草稿，完成校验后保存';
 const body=document.createElement('div');body.className='generation-preview-text';
 body.textContent=text||'正在准备内容…';panel.append(label,body);
 const main=document.querySelector('#main');main?.insertBefore(panel,main.children[1]||null);
};
