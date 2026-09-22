'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const nodes=new Map();let readingNodes=[];
function node(selector){if(!nodes.has(selector))nodes.set(selector,{innerHTML:'',textContent:'',hidden:false,scrollTop:0,classList:{toggle(){}},dataset:{},querySelectorAll(){return [];}});return nodes.get(selector);}
const context=vm.createContext({console,setTimeout,clearTimeout,window:{},document:{
 querySelector:node,querySelectorAll(selector){return selector==='[data-reading-text]'?readingNodes:[];},
 addEventListener(){},body:{classList:{toggle(){}}}
}});
vm.runInContext(fs.readFileSync('ui/app.js','utf8').split("$('#settings-nav').innerHTML=")[0],context);
vm.runInContext(fs.readFileSync('ui/learning.js','utf8'),context);
const execute=code=>vm.runInContext(code,context);
async function main(){
 execute("state={stats:{},profile:{},annotations:[]};page='chat';scene='cafe';run=async(label,work)=>work();calls=[];");
 context.snapshot={state:{session:'cafe',status:'free',goals:[]},memory:{},exposure_refs:['message:1','message:2'],messages:[1,2].map(id=>({role:'assistant',message_id:id,jp:'水',kana:'みず',zh:'水',feedback:'',suggestion:''}))};
 execute("rpc=async(action)=>{calls.push(action);if(action!=='chat_snapshot')throw new Error(action);return snapshot;};");
 await execute('loadChat()');
 assert.equal(execute('calls.join(",")'),'chat_snapshot','displaying replies must not fan out encounter requests');
 assert.equal(execute('viewedSources.size'),2);
 execute("rpc=()=>new Promise(resolve=>{finishOldChat=resolve;});");
 const stale=execute('loadChat()');execute("navigationRun++;page='home';finishOldChat({...snapshot,state:{session:'obsolete'}});");await stale;
 assert.equal(execute('chatState.session'),'cafe');

 readingNodes=[{dataset:{readingText:'水'},innerHTML:''},{dataset:{readingText:'猫'},innerHTML:''}];
 execute("page='lessons';lesson={id:'l'};calls=[];rpc=async(action,p)=>{calls.push(action);if(action!=='reading_lookup')throw new Error(action);const s=p.sentences[0];return {checked:[s],items:[{sentence:s,tokens:[{surface:s,lemma:'',reading:'',meaning:''}]}]};};");
 await execute('loadVisibleReadings()');await new Promise(setImmediate);
 assert.equal(execute('annotationChecked.size'),2,'budget-deferred sentences must be fetched in a subsequent batch');
 assert.equal(execute('calls.length'),2);
 assert.equal(readingNodes[1].innerHTML,'猫');
 readingNodes=[{dataset:{readingText:'新しい文'},innerHTML:''}];
 execute("lessonTab=()=>'<p>listening</p>';noteExposure=async()=>{};setLessonTab('speaking');");await new Promise(setImmediate);
 assert.equal(execute("annotationCache.has('新しい文')"),true,'switching lesson tabs must restore cached annotations');

 execute("calls=[];rpc=async(action,p)=>{calls.push(p.refresh);return {word:'猫',reading:'ねこ',meaning:'猫',model:'fixture'};};");
 await execute('loadDailyWord()');await execute('loadDailyWord(true)');
 assert.equal(execute('JSON.stringify(calls)'),'[false,true]');
 assert.match(execute('dailyWordView()'),/换一个/);
 console.log('Chat batching, stale responses, deferred annotations, lesson tabs and daily cache controls passed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
