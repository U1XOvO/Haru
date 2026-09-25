'use strict';
let speechSettings=null;
let speechDraftKey='';

function speechOption(value,label,current){
 return '<option value="'+value+'" '+(value===current?'selected':'')+'>'+label+'</option>';
}

function speechVoiceView(voice,selected){
 const builtIn=['girl','student','boy'].includes(voice.id);
 let html='<section class="speech-voice" data-voice-id="'+esc(voice.id)+'"><div class="speech-voice-heading"><label class="speech-select"><input type="radio" name="selected" value="'+esc(voice.id)+'" '+(selected===voice.id?'checked':'')+'><span><strong>'+esc(voice.name)+'</strong><small>'+esc(voice.theme||'自定义主题')+' · '+(voice.voice_id?'音色已生成':'音色待生成')+'</small></span></label>';
 if(!builtIn)html+='<button class="btn small" type="button" data-speech-action="remove" data-voice="'+esc(voice.id)+'">删除</button>';
 html+='</div>';
 if(!builtIn){
  html+='<div class="speech-voice-fields"><label>音色名称<input name="voice_name" value="'+esc(voice.name)+'" maxlength="40" required></label>';
  html+='<label>音色类型<select name="voice_gender">'+speechOption('female','女性',voice.gender)+speechOption('male','男性',voice.gender)+speechOption('neutral','中性',voice.gender)+'</select></label>';
  html+='<label class="speech-wide">声音描述<textarea name="voice_description" maxlength="500" rows="3" required>'+esc(voice.description)+'</textarea></label>';
  html+='<label>试音主题<input name="voice_theme" value="'+esc(voice.theme)+'" maxlength="60"></label>';
  html+='<label class="speech-wide">试音日文<textarea name="voice_sample" lang="ja" maxlength="1200" rows="6" required>'+esc(voice.sample)+'</textarea></label></div>';
 }else{
  html+='<p class="hint">'+esc(voice.description)+'</p>';
  html+='<details><summary>查看试音文本</summary><p class="speech-sample" lang="ja">'+esc(voice.sample)+'</p></details>';
 }
 html+='<div class="speech-voice-actions"><button class="btn small soft" type="button" data-speech-action="create" data-voice="'+esc(voice.id)+'">'+(voice.voice_id?'重新生成音色':'生成音色')+'</button><button class="btn small" type="button" data-speech-action="preview" data-voice="'+esc(voice.id)+'">'+icon('play')+' 试音</button></div></section>';
 return html;
}

function speechSettingsView(){
 if(!speechSettings)return '<h3>朗读与声音</h3><p class="hint" role="status">正在读取朗读配置…</p>';
 const s=speechSettings;
 return '<div class="speech-heading"><span class="ai-settings-mark" aria-hidden="true">'+icon('sound')+'</span><div><h3>朗读与声音</h3><p>选择声音，听见喜欢的日语。</p></div></div>'
  +'<form id="speech-settings-form" autocomplete="off"><fieldset class="speech-settings-fields">'
  +'<div class="speech-engine-choice"><label><input type="radio" name="engine" value="edge" '+(s.engine==='edge'?'checked':'')+'> Edge TTS</label><label><input type="radio" name="engine" value="gemini" '+(s.engine==='gemini'?'checked':'')+'> Gemini 3.8 Flash TTS</label></div>'
  +'<p class="hint">Gemini 朗读需使用你自己的 Google AI Studio API Key。调用失败时会尝试 Edge TTS；两者都需要网络，缓存过的音频可离线播放。</p>'
  +'<label>Google AI Studio API Key<input type="password" name="google_key" value="" autocomplete="new-password" placeholder="'+(s.key_configured?'已配置，留空保持原 Key':'粘贴你的 API Key')+'" maxlength="4096"></label>'
  +'<label class="check-label"><input type="checkbox" name="clear_key">清除已保存的 Google Key</label>'
  +'<div class="speech-options"><label>朗读节奏<select name="pace">'+speechOption('slow','慢',s.pace)+speechOption('slightly_slow','稍慢',s.pace)+speechOption('normal','自然',s.pace)+speechOption('slightly_fast','稍快',s.pace)+speechOption('fast','快',s.pace)+'</select></label>'
  +'<label>表达方式<select name="mood">'+speechOption('calm','平静',s.mood)+speechOption('gentle','温柔',s.mood)+speechOption('lively','活泼',s.mood)+'</select></label>'
  +'<label>发音方式<select name="clarity">'+speechOption('learning','适合跟读',s.clarity)+speechOption('natural','自然对话',s.clarity)+'</select></label>'
  +'<label>Google 超时（秒）<input type="number" name="timeout" min="10" max="90" step="1" value="'+s.timeout+'"></label>'
  +'<label>临时错误重试次数<select name="retries">'+speechOption('0','0 次',String(s.retries))+speechOption('1','1 次',String(s.retries))+'</select></label></div>'
  +'<label>自定义朗读方式<textarea name="style" maxlength="200" rows="2" placeholder="例如：像耐心的同学一样，句间稍作停顿">'+esc(s.style)+'</textarea></label>'
  +'<p class="hint">Gemini 的节奏由模型控制，不能保证精确的播放倍率。上方学习偏好中的倍速用于 Edge TTS。试音会保存当前设置并选择 Gemini。</p>'
  +'<div class="speech-list-heading"><strong>声音预设</strong><button type="button" class="btn small soft" data-speech-action="add" '+(s.voices.length>=20?'disabled':'')+'>＋ 自定义新声音</button></div>'
  +'<div class="speech-voice-list">'+s.voices.map(v=>speechVoiceView(v,s.selected)).join('')+'</div>'
  +'<div class="speech-actions"><button type="button" class="btn small" data-speech-action="refresh">'+icon('refresh')+' 重新读取</button><button type="button" class="btn small" data-speech-action="stop">'+icon('stop')+' 停止播放</button><button type="submit" class="btn primary">保存朗读设置</button></div>'
  +'</fieldset><p class="hint" id="speech-preview-status" role="status" aria-live="polite"></p></form>';
}

function collectSpeechSettings(form){
 const voices=[...form.querySelectorAll('[data-voice-id]')].map(section=>{
  const id=section.dataset.voiceId;
  const previous=speechSettings.voices.find(v=>v.id===id);
  if(['girl','student','boy'].includes(id))return previous;
  const field=name=>section.querySelector('[name="'+name+'"]').value;
  return {id,name:field('voice_name'),gender:field('voice_gender'),
          description:field('voice_description'),theme:field('voice_theme'),
          sample:field('voice_sample')};
 });
 const value=name=>form.querySelector('[name="'+name+'"]').value;
 return {revision:speechSettings.revision,engine:form.querySelector('[name="engine"]:checked').value,
         key:value('google_key'),clear_key:form.querySelector('[name="clear_key"]').checked,
         selected:form.querySelector('[name="selected"]:checked').value,
         pace:value('pace'),mood:value('mood'),clarity:value('clarity'),style:value('style'),
         timeout:Number(value('timeout')),retries:Number(value('retries')),voices};
}

function paintSpeechSettings(message=''){
 const target=document.querySelector('#speech-settings');
 if(target){target.innerHTML=speechSettingsView();target.querySelector('#speech-preview-status').textContent=message;}
}

async function loadSpeechSettings(){
 const target=document.querySelector('#speech-settings');
 if(!target)return;
 await run('正在读取朗读配置…',async()=>{
  try{speechSettings=await rpc('speech_settings_get');if(target.isConnected)paintSpeechSettings();}
  catch(error){if(target.isConnected)target.innerHTML='<h3>朗读与声音</h3><p class="hint" role="alert">'+esc(error.message)+'</p><button class="btn small" data-speech-action="refresh">重新读取</button>';throw error;}
 });
}

async function saveSpeechSettings(form){
 const params=collectSpeechSettings(form);
 const saved=await rpc('speech_settings_save',params);
 speechSettings=saved;
 speechDraftKey='';
 const input=form.querySelector('[name="google_key"]');
 if(input)input.value='';
 params.key='';
 paintSpeechSettings('朗读设置已保存。');
 return saved;
}

document.addEventListener('submit',async event=>{
 if(event.target.id!=='speech-settings-form')return;
 event.preventDefault();
 const form=event.target;
 await run('正在保存朗读设置…',()=>saveSpeechSettings(form),form.querySelector('[type="submit"]'));
});

document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-speech-action]');
 if(!button||button.disabled)return;
 const action=button.dataset.speechAction;
 if(action==='refresh'){await loadSpeechSettings();return;}
 if(action==='stop'){
  await rpc('study_stop_audio').catch(error=>toast(error.message,true));
  const status=document.querySelector('#speech-preview-status');
  if(status)status.textContent='已停止播放。';
  return;
 }
 const form=button.closest('#speech-settings-form');
 if(!form)return;
 if(action==='add'||action==='remove'){
  const draft=collectSpeechSettings(form);
  speechDraftKey=draft.key;
  speechSettings={...speechSettings,...draft};
  speechSettings.key_configured=speechSettings.key_configured&&!draft.clear_key;
  delete speechSettings.key;
  delete speechSettings.clear_key;
  if(action==='add'){
   const id='custom_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2,8);
   speechSettings.voices.push({id,name:'我的新声音',gender:'neutral',description:'',theme:'',sample:'',voice_id:''});
   speechSettings.selected=id;
  }else{
   speechSettings.voices=speechSettings.voices.filter(v=>v.id!==button.dataset.voice);
   if(speechSettings.selected===button.dataset.voice)speechSettings.selected='girl';
  }
  paintSpeechSettings();
  document.querySelector('#speech-settings [name="google_key"]').value=speechDraftKey;
  return;
 }
 if(action==='preview'||action==='create'){
  if(!form.reportValidity())return;
  const id=button.dataset.voice;
  const choice=[...form.querySelectorAll('[name="selected"]')].find(item=>item.value===id);
  if(choice)choice.checked=true;
  if(action==='preview')form.querySelector('[name="engine"][value="gemini"]').checked=true;
  await run(action==='create'?'正在生成声音…':'正在准备试音…',async()=>{
   await saveSpeechSettings(form);
   const voice=speechSettings.voices.find(v=>v.id===id);
   if(action==='create'){
    if(!speechSettings.key_configured)throw new Error('请先填写 Google AI Studio API Key。');
    await rpc('speech_voice_create',{id,force:!!voice.voice_id});
    speechSettings=await rpc('speech_settings_get');
    paintSpeechSettings('音色已生成，可以试听。');
   }else{
    if(!haruHasNative())throw new Error('试音请在 Haru 桌面 App 中使用。');
    const result=await rpc('speak',{text:voice.sample,rate:0.42*(state?.profile?.speech_rate??1)});
    const status=result?.fallback?'Gemini 试音失败（'+result.fallback+'），当前播放 Edge TTS。':
                 result?.engine==='gemini'?'正在播放 Gemini 试音。':'正在播放 Edge TTS 试音。';
    paintSpeechSettings(status);
   }
  },button);
 }
});
