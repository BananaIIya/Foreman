import assert from 'node:assert/strict';
import {routineWorkerTool} from './cursor_worker.mjs';
const request={budget_usd:1,in_turn_worker_enabled:true,in_turn_worker_continuation_reserve_usd:.1,
 in_turn_worker_tool:{description:'routine work',inputSchema:{}}};
let calls=0,boundary=null;
const make=(invoke,options={})=>routineWorkerTool({request,invoke,spent:()=>.2,reviewCost:()=>.005,
 blocked:()=>false,remoteBusy:()=>false,stop:x=>boundary=x,...options});
const paid=make(async()=>{calls++;return {model_calls:1,actual_cost_usd:.15,usage_complete:true,evidence:{exit_code:1}};});
assert.equal((await paid.tool.execute({})).evidence.exit_code,1);
assert.equal((await paid.tool.execute({})).model_calls,0);assert.equal(calls,1);assert.equal(paid.state.cost,.15);
for(const fee of [null,NaN,-1]){
 boundary=null;const w=make(async()=>({model_calls:1,actual_cost_usd:fee,usage_complete:true}));
 await w.tool.execute({});assert.equal(w.state.usageComplete,false);assert.equal(boundary,'unknown_delegated_worker_usage');
}
boundary=null;const over=make(async()=>({model_calls:1,actual_cost_usd:.9,usage_complete:true}));
await over.tool.execute({});assert.equal(over.state.cost,.9);assert.equal(boundary,'combined_worker_budget_exceeded');
const unavailable=make(async()=>{throw new Error('Must not execute');},{remoteBusy:()=>true});
assert.equal((await unavailable.tool.execute({})).model_calls,0);
let finish;const pending=make(()=>new Promise(r=>finish=r));const run=pending.tool.execute({});
assert.equal(pending.state.busy,true);assert.equal((await pending.tool.execute({})).model_calls,0);
finish({model_calls:1,actual_cost_usd:.1,usage_complete:true});await run;assert.equal(pending.state.busy,false);
console.log('Cursor worker checks passed: one call, pending exclusion, real errors, unknown fees, full overshoot.');
