// Mock-SDK check of the V59 transport resume. Runs advisor.mjs against a fake @cursor/sdk; no network, no models.
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const here=path.dirname(fileURLToPath(import.meta.url));
function scenario(mode){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'v59-resume-'));
 fs.mkdirSync(path.join(root,'v59_runtime'));
 for(const f of ['advisor.mjs','checkpoint.mjs'])fs.copyFileSync(path.join(here,f),path.join(root,'v59_runtime',f));
 const sdk=path.join(root,'v2_runtime/cursor_sdk/node_modules/@cursor/sdk/dist/esm');fs.mkdirSync(sdk,{recursive:true});
 fs.writeFileSync(path.join(sdk,'index.js'),`
const model={id:'grok-4.7',params:[{id:'context',value:'256k'},{id:'reasoning_effort',value:'high'},{id:'fast',value:'false'}]};
const usage={inputTokens:1000,outputTokens:100,cacheReadTokens:0,cacheWriteTokens:0,totalTokens:1100,reasoningTokens:50};
let sends=0;
export class JsonlLocalAgentStore{constructor(){}}
export const Agent={async create(){return {
 async send(prompt){sends++;
  if(sends===2&&process.env.MODE==='resume_throws')throw new Error('agent unusable');
  const fail=sends===1;
  return {async *stream(){yield {type:'usage',usage};},cancel:async()=>{},
   async wait(){return fail?{status:'error',error:{message:'[unknown] Premature close'},usage,model,result:''}
     :{status:'finished',usage,model,result:JSON.stringify({status:'completed',summary:'done',relevant_paths:[]})};}};},
 async [Symbol.asyncDispose](){}};}};`);
 const out=path.join(root,'phase');fs.mkdirSync(out);
 fs.writeFileSync(path.join(out,'request.json'),JSON.stringify({prompt:'task',effort:'high',budget_usd:1,command_limit:12,native_turn_limit:6,
  token_prices_per_million:{input:2,cache:.5,write:null,output:6}}));
 const r=spawnSync(process.execPath,[path.join(root,'v59_runtime/advisor.mjs'),out],{env:{...process.env,MODE:mode},input:''});
 const result=fs.existsSync(path.join(out,'sdk-result.json'))?JSON.parse(fs.readFileSync(path.join(out,'sdk-result.json'))):null;
 fs.rmSync(root,{recursive:true,force:true});
 return {code:r.status,stderr:String(r.stderr),result};
}
function assert(c,m){if(!c){console.error('FAIL',m);process.exit(1);}}
let s=scenario('resume_ok');
assert(s.code===0,'resume_ok exit '+s.stderr);
assert(s.result.transport_resumes===1&&s.result.turns.length===2,'one resume, two turns');
assert(s.result.status==='finished'&&s.result.usage.inputTokens===2000,'final finished and both turns billed');
s=scenario('resume_throws');
assert(s.code===0,'resume failure must not crash: '+s.stderr);
assert(s.result.boundary==='transport_resume_failed'&&s.result.usage===null,'failed resume marks usage unknown');
assert(s.result.known_cost_lower_bound_usd>0,'first turn cost kept as lower bound');
console.log('advisor resume tests OK');
