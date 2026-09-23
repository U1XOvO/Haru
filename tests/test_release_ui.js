'use strict';
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const code=fs.readFileSync('ui/release.js','utf8');
function make(overrides={}){
 const state={record:false,busyCount:0,studyGeneration:false,studyGenerationCancelling:false,
  studySaveFailed:false,studyClockFlight:false,dailyWordLoading:false,generatingLessons:new Set(),pending:new Map(),studyAttempt:null,...overrides};
 const context=vm.createContext({...state,window:{},document:{addEventListener(){},querySelector(){return {setAttribute(){},removeAttribute(){}};}},toast(){}});
 vm.runInContext(code,context);return context;
}
for(const state of [{record:true},{busyCount:1},{dailyWordLoading:true},{studyGeneration:true},{studySaveFailed:true},{pending:new Map([[1,{}]])},{studyAttempt:{status:'active'}}]){
 assert.equal(make(state).window.haruPrepareUpdate(),false);
}
const idle=make();assert.equal(idle.window.haruPrepareUpdate(),true);assert.equal(idle.window.haruPrepareUpdate(),false);
idle.window.haruCancelUpdate();assert.equal(idle.window.haruPrepareUpdate(),true);
assert.equal(make({studyAttempt:{status:'submitted',result:{}}}).window.haruPrepareUpdate(),true);
console.log('Update busy-state and resume checks passed');
