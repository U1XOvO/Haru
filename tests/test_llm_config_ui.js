'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const app=fs.readFileSync('ui/app.js','utf8').split("$('#settings-nav').innerHTML=")[0];
const nodes=new Map(),listeners={};
function node(s){if(!nodes.has(s))nodes.set(s,{innerHTML:'',textContent:'',classList:{toggle(){}}});return nodes.get(s);}
const ctx=vm.createContext({console,setTimeout,clearTimeout,window:{},document:{querySelector:node,addEventListener(type,fn){(listeners[type]??=[]).push(fn);},body:{classList:{toggle(){}}}}});
vm.runInContext(app,ctx);
const run=s=>vm.runInContext(s,ctx);
ctx.config={revision:0,active:'first',providers:[{id:'first',name:'<provider>',model:'model',base:'https://example.com/v1',key_configured:true,timeout:60,retries:1,reasoning:'auto'}]};
run('aiConfig=config');
assert.match(run('aiConfigView()'),/&lt;provider&gt;/);
assert.match(run('aiConfigView()'),/data-ai-add/);
assert.match(run('aiConfigView()'),/name="max_tokens"/);
assert.match(run('aiConfigView()'),/仅支持 OpenAI 兼容接口/);
assert.match(run('aiConfigView()'),/普通教学、词卡、注音、周测、连接测试/);
assert.match(run('aiConfigView()'),/沉浸故事<\/td><td>0\.55<\/td><td>16000/);
assert.match(run('aiConfigView()'),/自定义思考强度/);
assert.doesNotMatch(run('aiConfigView()'),/<option value="auto"/);
assert.doesNotMatch(run('aiConfigView()'),/参数适配|输出上限字段|其他参数|frequency_penalty|presence_penalty|name="seed"/);
function formFor(rows,active){
 const fields={disabled:false},result={textContent:''};
 const sections=rows.map(row=>({dataset:{providerId:row.id},querySelector(selector){
  const name=selector.match(/name="([^"]+)/)[1];
  return {value:row[name]==null?'':String(row[name]),checked:!!row[name]};
 }}));
 return {isConnected:false,querySelector(s){if(s==='fieldset')return fields;if(s==='#ping-result')return result;if(s.includes(':checked'))return {value:active};},querySelectorAll(s){return s==='[data-provider-id]'?sections:[];}};
}
ctx.form=formFor([{...ctx.config.providers[0],key:'temporary',temperature:0,top_p:'',max_tokens:200}], 'first');
const collected=JSON.parse(run('JSON.stringify(collectAIConfig(form))'));
assert.equal(collected.providers[0].temperature,0);
assert.equal(collected.providers[0].top_p,null);
ctx.button={disabled:false,closest(){return ctx.form;},hasAttribute(){return true;}};
listeners.click.forEach(listener=>listener({target:{closest(selector){return selector==='[data-ai-add],[data-ai-remove]'?ctx.button:null;}}}));
assert.equal(run('aiConfig.providers.length'),2);
assert.equal(run('aiConfig.providers[0].key'),'temporary','adding a provider preserves unsaved input');
const added=run('aiConfig.providers[1].id');
ctx.form=formFor(JSON.parse(run('JSON.stringify(aiConfig.providers)')),added);
ctx.button={disabled:false,dataset:{aiRemove:added},closest(){return ctx.form;},hasAttribute(){return false;}};
listeners.click.forEach(listener=>listener({target:{closest(selector){return selector==='[data-ai-add],[data-ai-remove]'?ctx.button:null;}}}));
assert.equal(run('aiConfig.providers.length'),1);
assert.equal(run('aiConfig.active'),'first','removing default selects a remaining provider');
async function main(){
 ctx.form=formFor(JSON.parse(run('JSON.stringify(aiConfig.providers)')),'first');
 run("run=async(label,fn)=>fn();rpc=async(action,params)=>{if(action!=='config_save')throw Error('Unexpected API call');savedParams=params;return {revision:1,active:'first',providers:[{...aiConfig.providers[0],key:undefined,key_configured:true}]};};");
 await run('saveAIConfig(form,false)');
 assert.equal(run('aiConfig.revision'),1);
 assert.equal(run('savedParams.providers[0].key'),'');
 assert.equal(run('aiConfig.providers[0].key'),undefined);
 console.log('Provider editor: escaping, optional values, add/remove, active selection, save and secret cleanup passed');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
