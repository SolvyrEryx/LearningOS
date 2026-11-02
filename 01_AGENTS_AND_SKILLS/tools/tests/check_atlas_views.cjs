/* Render-function smoke checks, not a substitute for a real browser layout review. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const input=process.argv[2];
if(!input)throw new Error('Pass the generated Atlas HTML path.');
const html=fs.readFileSync(input,'utf8');
const match=html.match(/<script id="atlas-data" type="application\/json">([\s\S]*?)<\/script>/);
assert(match,'Embedded data is present');
const source=fs.readFileSync(path.resolve(__dirname,'../../templates/atlas/atlas.js'),'utf8');
const nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{innerHTML:'',textContent:'',style:{},value:'',open:false,showModal(){this.open=true},close(){this.open=false},focus(){},setSelectionRange(){}});return nodes.get(id)};
node('atlas-data').textContent=match[1];
const context=vm.createContext({document:{getElementById:node,querySelector:s=>node(s.slice(1)),addEventListener(){}},window:{addEventListener(){},scrollTo(){}},location:{hash:''},navigator:{},setTimeout(){},console});
vm.runInContext(source,context);
for(const view of ['today','map','evidence','revision','tests','errors','progress','memory','library','health','guide']){
 vm.runInContext(`render('${view}')`,context);
 assert(node('content').innerHTML.length>100,view+' renders useful content');
 assert(!node('content').innerHTML.includes('NaN'),view+' has no invalid numbers');
 console.log('OK '+view);
}
vm.runInContext("if(D.concepts.length)conceptDetail(D.concepts[0].concept_id)",context);
assert(!node('detail-content').innerHTML.includes('undefined'));
// Exercise empty data states separately without writing fabricated learner memory.
vm.runInContext("D.concepts=[];D.tests=[];D.events=[];D.subjects=[];D.exams=[];D.sources=[];D.runs=[];D.tasks=null;D.question_count=0;memoryCache=null",context);
for(const view of ['today','map','evidence','revision','tests','errors','progress','memory','library','guide'])vm.runInContext(`render('${view}')`,context);
console.log('OK empty states and concept detail');
