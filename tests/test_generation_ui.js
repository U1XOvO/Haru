'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const nodes=new Map();
function element(){return {children:[],className:'',textContent:'',setAttribute(){},append(...items){this.children.push(...items);},remove(){nodes.delete('#'+this.id);}};}
const main=element();main.children=[element()];main.insertBefore=function(el){nodes.set('#'+el.id,el);};nodes.set('#main',main);
const ctx=vm.createContext({window:{},document:{createElement:element,querySelector:s=>nodes.get(s)||null},page:'immersion',navigationRun:3});
vm.runInContext(fs.readFileSync('ui/generation.js','utf8'),ctx);
const event={event:'generation',preview:{title:'<img onerror=alert(1)>',sentences:[{jp:'こんにちは',zh:'你好'}]}};
ctx.window.haruGenerationEvent(1,event,{action:'immersion',page:'immersion',nav:3});
let panel=nodes.get('#generation-preview');assert.ok(panel);assert.match(panel.children[1].textContent,/<img onerror/);
assert.equal(panel.children[1].innerHTML,undefined,'untrusted content is only text');
ctx.navigationRun=4;ctx.window.haruGenerationPaint();assert.equal(nodes.has('#generation-preview'),false,'navigation suppresses stale previews');
ctx.window.haruGenerationDone(1);ctx.navigationRun=3;ctx.window.haruGenerationPaint();assert.equal(nodes.has('#generation-preview'),false);
ctx.window.haruGenerationEvent(2,event,{action:'study_generation_step',page:'immersion',nav:3});assert.equal(nodes.has('#generation-preview'),false,'exam drafts are never exposed');
console.log('Generation previews: early content, escaping, stale navigation, completion cleanup and exam isolation passed');
