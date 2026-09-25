'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const listeners={},status={textContent:''},input={value:'temporary-key'};
const target={isConnected:true,innerHTML:'',querySelector(selector){
 if(selector==='#speech-preview-status')return status;
 if(selector==='[name="google_key"]')return input;
}};
const config={
 revision:0,engine:'gemini',key_configured:true,selected:'girl',
 pace:'normal',mood:'gentle',clarity:'learning',style:'',timeout:45,retries:1,
 voices:[
  {id:'girl',name:'可爱年轻女声',gender:'female',description:'A youthful Japanese voice',
   theme:'春日野餐',sample:'今日は公園へ行きます。',voice_id:'voice_fixture'},
  {id:'student',name:'大学女生',gender:'female',description:'A student',
   theme:'校园一天',sample:'大学で勉強します。',voice_id:''},
  {id:'boy',name:'清爽年轻男声',gender:'male',description:'A young adult man',
   theme:'周末出游',sample:'電車に乗ります。',voice_id:''},
  {id:'custom_one',name:'<script>oops</script>',gender:'neutral',
   description:'A custom <voice>',theme:'夕食',sample:'食事をします。',voice_id:''}
 ]
};
const calls=[];
const context=vm.createContext({
 console,Date,Math,document:{
  querySelector(selector){return selector==='#speech-settings'?target:null;},
  addEventListener(type,callback){(listeners[type]??=[]).push(callback);}
 },icon:()=>'<svg></svg>',
 esc:value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
 haruHasNative:()=>true,
 state:{profile:{speech_rate:1}},
 run:async(_label,fn)=>fn(),
 toast:()=>{},
 rpc:async(action,params)=>{
  calls.push({action,params:params&&JSON.parse(JSON.stringify(params))});
  if(action==='speech_settings_save')return {...config,revision:config.revision+1,key_configured:true};
  if(action==='speak')return {engine:'edge',fallback:'Google 配额已达上限'};
  throw Error('Unexpected RPC '+action);
 }
});
vm.runInContext(fs.readFileSync('ui/speech_settings.js','utf8'),context);
context.fixture=config;
vm.runInContext('speechSettings=fixture',context);
const html=vm.runInContext('speechSettingsView()',context);
assert.match(html,/可爱年轻女声/);
assert.match(html,/大学女生/);
assert.match(html,/清爽年轻男声/);
assert.match(html,/自定义新声音/);
assert.match(html,/春日野餐/);
assert.match(html,/&lt;script&gt;oops&lt;\/script&gt;/);
assert.doesNotMatch(html,/<script>oops<\/script>|temporary-key/);
const radio={value:'girl',checked:true};
const engine={value:'gemini',checked:true};
const fields={
 engine,google_key:input,clear_key:{checked:false},selected:radio,
 pace:{value:'normal'},mood:{value:'gentle'},clarity:{value:'learning'},
 style:{value:'like a patient teacher'},timeout:{value:'45'},retries:{value:'1'}
};
const sections=config.voices.map(voice=>({dataset:{voiceId:voice.id},
 querySelector(selector){
  const name=selector.match(/name="([^"]+)"/)[1];
  return {value:({
   voice_name:voice.name,voice_gender:voice.gender,voice_description:voice.description,
   voice_theme:voice.theme,voice_sample:voice.sample
  })[name]};
 }}));
const form={reportValidity:()=>true,querySelector(selector){
 if(selector==='[name="engine"]:checked')return engine;
 if(selector==='[name="selected"]:checked')return radio;
 if(selector==='[name="engine"][value="gemini"]')return engine;
 return fields[selector.match(/name="([^"]+)"/)[1]];
 },querySelectorAll(selector){
  if(selector==='[data-voice-id]')return sections;
  if(selector==='[name="selected"]')return [radio];
  return [];
 }};
async function main(){
 await vm.runInContext('saveSpeechSettings(form)',Object.assign(context,{form}));
 assert.equal(calls[0].action,'speech_settings_save');
 assert.equal(calls[0].params.key,'temporary-key');
 assert.equal(input.value,'');
 assert.equal(vm.runInContext('speechSettings.key',context),undefined);
 assert.equal(status.textContent,'朗读设置已保存。');
 const button={disabled:false,dataset:{speechAction:'preview',voice:'girl'},closest:()=>form};
 const event={target:{closest:()=>button}};
 await listeners.click[0](event);
 assert.equal(calls.at(-1).action,'speak');
 assert.match(status.textContent,/Gemini 试音失败.*Edge TTS/);
 assert.doesNotMatch(status.textContent,/temporary-key/);
 console.log('Speech settings: escaped custom styles, local key handling, and honest fallback preview passed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
