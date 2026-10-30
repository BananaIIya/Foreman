// One tiny, labeled capability probe. No task files, shell tools or task answers.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {Agent, JsonlLocalAgentStore} from './v2_runtime/cursor_sdk/node_modules/@cursor/sdk/dist/esm/index.js';
const root=path.dirname(new URL(import.meta.url).pathname);
const trial=path.join(root,'raw/jobs/transport-probe-cursor-system-prompt/fixture');
fs.mkdirSync(trial,{recursive:false});
const phase=path.join(trial,'agent/phases/worker-probe');fs.mkdirSync(phase,{recursive:true});
const apiKey=JSON.parse(fs.readFileSync(process.env.CURSOR_AUTH_JSON,'utf8')).apiKey;
const privateDir=fs.mkdtempSync(path.join(os.tmpdir(),'cursor-system-probe-'));
const cwd=path.join(privateDir,'workspace');fs.mkdirSync(cwd);
const model={id:'grok-4.7',params:[{id:'context',value:'256k'},{id:'reasoning_effort',value:'low'},{id:'fast',value:'false'}]};
const systemPrompt='You are a text-only JSON protocol worker. Follow the requested JSON schema. No tools, shell commands, delegation or extra text.';
const prompt='Synthetic protocol test, not a benchmark task. Return exactly {"ok":true}.';
function save(file,value){fs.writeFileSync(file,JSON.stringify(value,null,2).replaceAll(apiKey,'[REDACTED]')+'\n');}
save(path.join(trial,'config.json'),{kind:'native-sdk-capability-probe-not-harbor',model,tools:[],benchmark_task:false});
save(path.join(phase,'request.json'),{model:'grok-4.7',provider:'cursor-sdk',effort:'low',systemPrompt,prompt,tools:[],native_call_limit:1});
let agent,raw,error;let toolCalls=0;const usageEvents=[];
try{
 agent=await Agent.create({apiKey,model,systemPrompt,tools:[],disallowedTools:['task','mcp'],local:{cwd,settingSources:[],store:new JsonlLocalAgentStore(path.join(privateDir,'store')),enableAgentRetries:false}});
 const run=await agent.send(prompt);
 for await(const event of run.stream()){
  const safe=event.type==='thinking'?{type:'thinking',characters:event.text?.length??0}:event;
  fs.appendFileSync(path.join(phase,'sdk-events.jsonl'),JSON.stringify(safe).replaceAll(apiKey,'[REDACTED]')+'\n');
  if(event.type==='tool_call')toolCalls++;
  if(event.type==='usage')usageEvents.push(event.usage);
 }
 raw=await run.wait();
}catch(exc){error={name:exc.name,message:String(exc.message).replaceAll(apiKey,'[REDACTED]')};}
finally{
 if(agent){try{await agent[Symbol.asyncDispose]();}catch(exc){error??={name:exc.name,message:String(exc.message).replaceAll(apiKey,'[REDACTED]')};}}
 fs.rmSync(privateDir,{recursive:true,force:true});
}
const usage=raw?.usage??null;
const cost=usage&&!usage.cacheWriteTokens?(usage.inputTokens*2+usage.cacheReadTokens*.5+usage.outputTokens*6)/1e6:null;
const parameters=raw?.model?.params??[];
const modelMatched=raw?.model?.id===model.id&&model.params.every(p=>parameters.some(x=>x.id===p.id&&x.value===p.value));
let jsonAnswer;try{jsonAnswer=JSON.parse(raw?.result??'');}catch{}
const passed=raw?.status==='finished'&&modelMatched&&toolCalls===0&&jsonAnswer?.ok===true&&usage!==null&&!(usage.cacheWriteTokens);
save(path.join(phase,'sdk-result.json'),{...raw,error:error??raw?.error,toolCalls,usageEvents});
save(path.join(phase,'phase-result.json'),{status:passed?'completed':'error',usage:{cost_usd:cost,native_usage:usage},evidence:{text:raw?.result??'',commands:[],command_count:0},native_error:error??raw?.error});
const report={kind:'native-sdk-capability-probe-not-harbor',benchmark_task:false,passed,toolCalls,modelMatched,native_cost_usd:cost,usage,error:error??raw?.error};
save(path.join(trial,'result.json'),report);
console.log(JSON.stringify(report));
