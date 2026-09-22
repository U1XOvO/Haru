'use strict';
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const code=fs.readFileSync('ui/study.js','utf8');
const clone=value=>JSON.parse(JSON.stringify(value));
function attempt(){return {id:'a1',mode:'practice',status:'active',revision:0,section_index:0,
 answers:{},flags:[],elapsed:0,deadline:null,paper:{id:'p1',source_type:'ai',level:'N5',
 sections:[{id:'one',seconds:30},{id:'two',seconds:30}],questions:[{id:'q0',section:'one'},{id:'q1',section:'two'}]}};}
function harness(handler){
 const elements=new Map([['#study-save-status',{textContent:''}],['#study-answer-count',{textContent:''}]]);
 const radios=[0,1].map(value=>({value:String(value),checked:false,chosen:false,closest(){return {classList:{toggle:(_,on)=>{this.chosen=on;}}};}}));
 const palette=[0,1].map(index=>({dataset:{index:String(index)},answered:false,classList:{toggle(_,on){palette[index].answered=on;}}}));
 const storage=new Map(),listeners={},intervals=[];const stats={renders:0,requests:[],toasts:[]};
 const context=vm.createContext({page:'jlpt',localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
  document:{hidden:false,addEventListener:(kind,fn)=>(listeners[kind]??=[]).push(fn),querySelectorAll:selector=>selector.startsWith('input')?radios:palette},
  $:selector=>elements.get(selector)||null,setInterval:(fn,delay)=>intervals.push({fn,delay}),
  render:()=>stats.renders++,toast:(...args)=>stats.toasts.push(args),
  rpc:async(action,params)=>{stats.requests.push({action,params:clone(params)});return handler(action,params);},
  console});
 vm.runInContext(code,context);context.fixture=attempt();vm.runInContext('studyRememberAttempt(fixture)',context);
 return {context,stats,radios,palette,elements,intervals,listeners,run:s=>vm.runInContext(s,context)};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
async function main(){
 const responses=[];
 const h=harness((action,params)=>new Promise(resolve=>responses.push({action,params,resolve})));
 const first=h.run('studyWrite({answers:{q0:0}})'),second=h.run('studyWrite({answers:{q0:1}})');
 await flush();assert.equal(responses.length,1,'writes must remain serialized');
 assert.equal(h.radios[1].checked,true,'latest pending selection stays selected');
 responses[0].resolve({id:'a1',delta:true,revision:1,status:'active',section_index:0,answers:{q0:0},flags:[],elapsed:1});
 await first;await flush();assert.equal(responses.length,2);assert.equal(responses[1].params.revision,1);
 assert.equal(h.radios[1].checked,true,'an older response must not undo a queued answer');
 responses[1].resolve({id:'a1',delta:true,revision:2,status:'active',section_index:0,answers:{q0:1},flags:[],elapsed:2});
 await second;
 assert.equal(h.stats.renders,0,'normal answers must not replace the page');
 assert.equal(h.run('studyAttempt.paper.id'),'p1');assert.equal(h.palette[0].answered,true);
 assert.equal(h.elements.get('#study-answer-count').textContent,'已答 1/2');
 const heartbeat=h.intervals.find(x=>x.delay===20000);heartbeat.fn();await flush();
 assert.equal(responses.length,3);assert.equal(responses[2].params.revision,2);
 assert.equal('answers' in responses[2].params,false);
 responses[2].resolve({id:'a1',delta:true,revision:3,status:'active',section_index:0,answers:{q0:1},flags:[],elapsed:22});
 await h.run('studyQueue');assert.equal(h.stats.renders,0);assert.equal(h.run('studyAttempt.elapsed'),22);

 const submitted={...attempt(),status:'submitted',revision:2,result:{correct:1,total:2},paper:{...attempt().paper,questions:[{id:'q0',section:'one',answer:0},{id:'q1',section:'two',answer:0}]}};
 const submit=harness(async(action,params)=>action==='study_attempt'?clone(submitted):{id:'a1',delta:true,status:'submitted',revision:2,result:submitted.result,answers:{q0:0},flags:[],section_index:0});
 await submit.run('studyWrite({finish:true})');
 assert.deepEqual(submit.stats.requests.map(x=>x.action),['study_save','study_attempt']);
 assert.equal(submit.stats.renders,1);assert.equal(submit.run('studyAttempt.paper.questions[0].answer'),0);
 assert.equal(submit.run('studyAttempt.delta'),undefined);

 const deadline=harness(async(action,params)=>({id:'a1',delta:true,status:'active',mode:'timed',revision:1,section_index:1,deadline:Date.now()/1000+30,answers:{},flags:[]}));
 deadline.run("studyAttempt.mode='timed';studyAttempt.deadline=0");
 deadline.intervals.find(x=>x.delay===1000).fn();await deadline.run('studyQueue');
 assert.equal(deadline.stats.requests[0].params.state_only,true);
 assert.equal(deadline.stats.renders,1);assert.equal(deadline.run('studyQuestion'),1);

 const failure=harness(async()=>{throw new Error('revision conflict');});
 await assert.rejects(failure.run('studyWrite({answers:{q0:1}})'),/revision conflict/);
 assert.equal(failure.run('studySaveFailed'),true);
 await assert.rejects(failure.run('studyWrite()'),/重新读取/);
 assert.equal(failure.stats.requests.length,1);
 assert.match(failure.elements.get('#study-save-status').textContent,/保存失败/);

 const paged=harness(async()=>{});
 paged.run("studyCatalog={papers_page:{next_offset:30,total:65},history_page:{next_offset:null,total:3}};");
 assert.match(paged.run("studyMoreButton('papers')"),/data-offset="30"/);
 assert.equal(paged.run("studyMoreButton('history')"),'');
 console.log('Study deltas, serialized writes, local answer updates, heartbeat, deadline and pagination checks passed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
