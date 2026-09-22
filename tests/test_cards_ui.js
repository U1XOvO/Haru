'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const app=fs.readFileSync('ui/app.js','utf8').split("$('#settings-nav').innerHTML=")[0];
const nodes=new Map();
function node(selector){if(!nodes.has(selector))nodes.set(selector,{innerHTML:'',textContent:'',hidden:true,classList:{toggle(){}},className:''});return nodes.get(selector);}
const context=vm.createContext({console,setTimeout,clearTimeout,window:{},document:{querySelector:node,addEventListener(){},body:{classList:{toggle(){}}}},pronunciationHTML(){return '';},distributionLocked:false});
vm.runInContext(app,context);
const execute=code=>vm.runInContext(code,context);
const card=(id,ready=true)=>({id,word:id,reading:'かな',romaji:'kana',meaning:'词义',example:'例文',translation:'例句',mnemonic:'记忆',source:'测试',due:'2026-09-22T00:00:00',ready});
context.first=card('first');context.second=card('second');
execute("state={stats:{cards:2,due:2,today:0}};page='cards';cardCounts={total:2,due:2};cardQueue=[first,second];cardList=[first,second];render=()=>{throw new Error('Card interaction replaced the whole page');};toast=()=>{};calls=[];");
async function main(){
 node('#main').innerHTML='keep current page';node('#card-search').value='keep search focus';
 execute("rpc=async(action,params)=>{calls.push(action);throw new Error('Unexpected RPC '+action);};");
 await execute("handleAction('flip',{})");
 assert.equal(execute('flipped'),true);assert.match(node('#card-review-panel').innerHTML,/词义/);
 assert.equal(node('#main').innerHTML,'keep current page');assert.equal(node('#card-search').value,'keep search focus');
 context.reviewed={...card('first',false),interval:1};
 execute("rpc=async(action,params)=>{calls.push(action);assertion=action;if(action!=='review')throw new Error('Unexpected RPC '+action);return {card:reviewed,due:reviewed.due,interval:1,counts:{total:2,due:1},stats:{cards:2,due:1,today:1}};};");
 await execute("reviewCard('first',4)");
 assert.equal(execute('calls.join(",")'),'review');assert.equal(execute('cardQueue.length'),1);
 assert.equal(execute('cardList[0].ready'),false);assert.equal(execute('state.stats.today'),1);
 assert.equal(node('#main').innerHTML,'keep current page');
 execute("calls=[];rpc=async(action)=>{calls.push(action);if(action==='review')return {card:{...second,ready:false},due:second.due,interval:1,counts:{total:3,due:1}};if(action==='card_queue')return {items:[first],counts:{total:3,due:1}};throw new Error(action);};");
 await execute("reviewCard('second',4)");
 assert.equal(execute('calls.join(",")'),'review,card_queue');assert.equal(execute('cardQueue[0].id'),'first');
 execute("rpc=()=>new Promise(resolve=>{pendingHistory=resolve;});cardSearch='old';");
 const pending=execute('loadCardHistory()');execute("cardSearch='new';cardHistoryRun++;pendingHistory({items:[],next_cursor:null,counts:{total:0,due:0}});");await pending;
 assert.equal(execute('cardList.length'),2,'outdated search response must be ignored');
 execute("rpc=async(action,params)=>{calls.push({action,params});return {items:[],next_cursor:null,counts:{total:3,due:1}};};calls=[];");
 await execute('loadCardHistory(50,[null])');
 assert.equal(execute('calls[0].action'),'cards_page');assert.equal(execute('calls[0].params.query'),'new');assert.equal(execute('cardPageBefore'),50);
 assert.equal(execute('cardPageStack.length'),1);assert.equal(node('#main').innerHTML,'keep current page');
 console.log('Card flip, incremental review, bounded refill, pagination and stale-search checks passed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
