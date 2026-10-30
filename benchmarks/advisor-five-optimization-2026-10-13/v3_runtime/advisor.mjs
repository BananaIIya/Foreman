import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Agent, JsonlLocalAgentStore } from '../v2_runtime/cursor_sdk/node_modules/@cursor/sdk/dist/esm/index.js';
const out=path.resolve(process.argv[2]);
const request=JSON.parse(fs.readFileSync(path.join(out,'request.json'),'utf8'));
const privateDir=fs.mkdtempSync(path.join(os.tmpdir(),'advisor-sdk-'));
const cwd=path.join(privateDir,'workspace');fs.mkdirSync(cwd);
const model={id:'grok-4.7',params:[{id:'context',value:'256k'},{id:'reasoning_effort',value:request.effort},{id:'fast',value:'false'}]};
let agent;let toolCalls=0;const usageEvents=[];
try {
 agent=await Agent.create({apiKey:process.env.CURSOR_API_KEY,model,tools:[],disallowedTools:['task','mcp'],local:{cwd,settingSources:[],store:new JsonlLocalAgentStore(path.join(privateDir,'store')),enableAgentRetries:false}});
 const run=await agent.send(request.prompt);
 for await(const event of run.stream()){
  const safe=event.type==='thinking'?{type:'thinking',characters:event.text?.length??0}:event;
  fs.appendFileSync(path.join(out,'sdk-events.jsonl'),JSON.stringify(safe)+'\n');
  if(event.type==='tool_call')toolCalls++;
  if(event.type==='usage')usageEvents.push(event.usage);
 }
 const result=await run.wait();
 fs.writeFileSync(path.join(out,'sdk-result.json'),JSON.stringify({...result,toolCalls,usageEvents},null,2)+'\n');
 if(toolCalls!==0)throw new Error('Unexpected advisor tool execution');
 if(result.model?.id!==model.id||!model.params.every(p=>result.model.params?.some(x=>x.id===p.id&&x.value===p.value)))throw new Error('Model/effort drift');
 console.log(JSON.stringify({status:result.status,toolCalls,usage:result.usage}));
} finally {
 if(agent)await agent[Symbol.asyncDispose]();
 fs.rmSync(privateDir,{recursive:true,force:true});
}
