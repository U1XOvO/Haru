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
 assert.equal(execute("navItems.some(item=>item[0]==='chat')"),false);
 assert.equal(execute("typeof chatView"),'undefined');
 execute("lesson={design_version:1,kind:'lesson',lesson_no:1,stage:1,title:'入门',goal:'问候',grammar:'语法',tip:'提示',examples:[],review_targets:[{word:'猫',covered:true}],source:'fixture'};");
 assert.match(execute('lessonBody()'),/本次复习词：猫/,'daily lesson body must render the shared review coverage helper');
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
 console.log('Deferred annotations, lesson tabs and daily cache controls passed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
