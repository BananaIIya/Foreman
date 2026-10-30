import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Agent, JsonlLocalAgentStore } from '@cursor/sdk';
const out=path.resolve('raw/preflight/cursor-sdk-no-tools-probe');
fs.mkdirSync(out,{recursive:true});
fs.writeFileSync(path.join(out,'launch-once.json'),JSON.stringify({at:new Date().toISOString(),sdk:'1.0.37'}),{flag:'wx'});
const privateDir=fs.mkdtempSync(path.join(os.tmpdir(),'advisor-sdk-probe-'));
const cwd=path.join(privateDir,'workspace');fs.mkdirSync(cwd);
const apiKey=JSON.parse(fs.readFileSync(process.env.CURSOR_AUTH_JSON,'utf8')).apiKey;
const model={id:'grok-4.7',params:[{id:'context',value:'256k'},{id:'reasoning_effort',value:'high'},{id:'fast',value:'false'}]};
const prompt='This is a small transport check, not a benchmark puzzle. All context is below; no tools are available or needed. Return JSON only with fields status="patch", files=[{path:"/probe/totals.py",content:COMPLETE_REPLACEMENT_SOURCE}], validation_commands=[], requested_evidence=[], handoff. Source: def sum_values(values):\\n    total = 0\\n    for value in values:\\n        total = value\\n    return total\\n . Fix it to return the sum, including 0 for an empty input and correct negative values. Only define sum_values; no imports or side effects. Do not claim to have executed checks.';
fs.writeFileSync(path.join(out,'request.json'),JSON.stringify({model,tools:[],settingSources:[],prompt},null,2));
let agent;const usageEvents=[];let toolCalls=0;const init=[];
try {
 agent=await Agent.create({apiKey,model,tools:[],disallowedTools:['task','mcp'],local:{cwd,settingSources:[],store:new JsonlLocalAgentStore(path.join(privateDir,'store')),enableAgentRetries:false}});
 const run=await agent.send(prompt);
 for await(const event of run.stream()){
  const safe=event.type==='thinking'?{type:'thinking',characters:event.text?.length??0}:event;
  fs.appendFileSync(path.join(out,'events.jsonl'),JSON.stringify(safe)+'\n');
  if(event.type==='tool_call')toolCalls++;
  if(event.type==='usage')usageEvents.push(event.usage);
  if(event.type==='system')init.push({model:event.model,tools:event.tools});
 }
 const result=await run.wait();
 const record={...result,toolCalls,usageEvents,init};
 fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(record,null,2)+'\n');
 if(result.status!=='finished'||toolCalls!==0||!result.usage)throw new Error('No-tools native probe did not finish cleanly with usage');
 if(result.model?.id!==model.id||!model.params.every(p=>result.model.params?.some(x=>x.id===p.id&&x.value===p.value)))throw new Error('Model/effort drift');
 console.log(JSON.stringify({status:result.status,model:result.model,toolCalls,usage:result.usage,usageEvents:usageEvents.length,result:result.result}));
} finally {
 if(agent)await agent[Symbol.asyncDispose]();
 fs.rmSync(privateDir,{recursive:true,force:true});
}
