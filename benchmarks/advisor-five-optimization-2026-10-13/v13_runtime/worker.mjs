import {parseCheckpoint} from '../v12_runtime/checkpoint.mjs';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import readline from 'node:readline';
import {Agent,JsonlLocalAgentStore} from '../v2_runtime/cursor_sdk/node_modules/@cursor/sdk/dist/esm/index.js';

const out=path.resolve(process.argv[2]);
const request=JSON.parse(fs.readFileSync(path.join(out,'request.json'),'utf8'));
const privateDir=fs.mkdtempSync(path.join(os.tmpdir(),'advisor-sdk-tools-'));
const cwd=path.join(privateDir,'workspace');fs.mkdirSync(cwd);
const model={id:'grok-4.7',params:[{id:'context',value:'256k'},{id:'reasoning_effort',value:request.effort},{id:'fast',value:'false'}]};
const pending=new Map();let sequence=0,commandCount=0,turnCommands=0,spent=0,boundary=null;
let agent,run;const usageEvents=[];const toolEvents=[];const turns=[];
const totalUsage={inputTokens:0,outputTokens:0,cacheReadTokens:0,cacheWriteTokens:0,totalTokens:0,reasoningTokens:0};
let usageComplete=true;
const input=readline.createInterface({input:process.stdin});
input.on('line',line=>{
 const reply=JSON.parse(line);const resolve=pending.get(reply.id);
 if(resolve){pending.delete(reply.id);resolve(reply.result);}
});
function relay(command){
 const id=String(sequence++);
 return new Promise(resolve=>{pending.set(id,resolve);process.stdout.write(JSON.stringify({type:'remote_command',id,command})+'\n');});
}
function cost(usage){
 const p=request.token_prices_per_million;
 if(usage.cacheWriteTokens&&p.write===null)return null;
 return (usage.inputTokens*p.input+usage.cacheReadTokens*p.cache+usage.cacheWriteTokens*(p.write??0)+usage.outputTokens*p.output)/1e6;
}
try{
 agent=await Agent.create({apiKey:process.env.CURSOR_API_KEY,model,tools:['mcp'],disallowedTools:['task'],mcpServers:{},
  local:{cwd,settingSources:[],store:new JsonlLocalAgentStore(path.join(privateDir,'store')),enableAgentRetries:false,
   customTools:{remote_command:{description:'Run one bash command ONLY inside the current task Daytona container, cwd /app. Files persist; shell variables do not. 300-second deadline. Output may be truncated with an explicit flag; print concise sufficient observations or page data yourself. No host access or other models. Use here-documents to write remote files. At most two commands total in this worker checkpoint; each command at most 6000 characters. Do one reliable, bounded piece of work, then inspect its actual result before choosing the next step. Escalate unresolved specialist reasoning instead of building a speculative full solver.',
    inputSchema:{type:'object',additionalProperties:false,required:['command'],properties:{command:{type:'string',maxLength:6000}}},
    execute:async args=>{
     if(boundary||commandCount>=request.command_limit||turnCommands>=2){
      boundary??='command_limit';void run.cancel().catch(()=>{});
      return {controller_error:'Intervention boundary reached; nothing executed'};
     }
     commandCount++;turnCommands++;
     return await relay(args.command);
    }}}}});
 const checkpoint=' This is one WORKER checkpoint with at most TWO remote_command calls and no automatic continuation. Use the tool for short, reliable progress and respond to actual outputs. Do not generate a complete speculative solver before seeing execution evidence. If you cannot confidently solve the specialist part, ask for help now. After these calls return compact JSON with status working (expert help or further work needed), completed (the required artifact is actually checked), or blocked, plus summary, relevant_paths, checks, critical_question and unresolved_assumptions. This vocabulary overrides earlier needs_advisor wording. Do not put executable scripts in final JSON; they belong only in remote commands. All uncertainty and failures will be passed to the decision model and advisor.';
 for(let index=0;index<request.native_turn_limit;index++){
  turnCommands=0;
  const turnDir=path.join(out,'turns',String(index));fs.mkdirSync(turnDir,{recursive:true});
  const prompt=(index===0?request.prompt:'Continue your same bounded specialist intervention from the actual observations and files already in this conversation.')+checkpoint+` Remaining intervention cost target: $${Math.max(0,request.budget_usd-spent)}.`;
  fs.writeFileSync(path.join(turnDir,'request.json'),JSON.stringify({model,turn_index:index,prompt,cost_target_usd:Math.max(0,request.budget_usd-spent)},null,2)+'\n');
  run=await agent.send({text:prompt,images:request.images??[]});
  for await(const event of run.stream()){
  const safe=event.type==='thinking'?{type:'thinking',characters:event.text?.length??0}:event;
  fs.appendFileSync(path.join(out,'sdk-events.jsonl'),JSON.stringify(safe)+'\n');
  fs.appendFileSync(path.join(turnDir,'sdk-events.jsonl'),JSON.stringify(safe)+'\n');
  if(event.type==='tool_call')toolEvents.push(event);
  if(event.type==='usage'){
   usageEvents.push(event.usage);
  }
  }
  const result=await run.wait();const answer=parseCheckpoint(result.result??'');const observed={...result,commandCount:turnCommands,checkpointStatus:answer?.status??null,checkpointParse:answer?'accepted':'unrecognized'};turns.push(observed);
  fs.writeFileSync(path.join(turnDir,'sdk-result.json'),JSON.stringify(observed,null,2)+'\n');
  if(result.model?.id!==model.id||!model.params.every(p=>result.model.params?.some(x=>x.id===p.id&&x.value===p.value)))throw new Error('Model/effort drift');
  const c=result.usage?cost(result.usage):null;
  if(c===null){usageComplete=false;boundary='unknown_usage';}
  else{spent+=c;for(const key of Object.keys(totalUsage))totalUsage[key]+=result.usage[key]??0;}
  if(spent>=request.budget_usd)boundary='cost_boundary';
  if(boundary||result.status!=='finished'||answer?.status!=='working')break;
  if(index+1===request.native_turn_limit)boundary='native_turn_limit';
 }
 const result=turns.at(-1);
 fs.writeFileSync(path.join(out,'sdk-result.json'),JSON.stringify({...result,usage:usageComplete?totalUsage:null,turns,usageEvents,toolEvents,commandCount,boundary,known_cost_lower_bound_usd:spent},null,2)+'\n');
}finally{
 input.close();
 if(agent)await agent[Symbol.asyncDispose]();
 fs.rmSync(privateDir,{recursive:true,force:true});
}
