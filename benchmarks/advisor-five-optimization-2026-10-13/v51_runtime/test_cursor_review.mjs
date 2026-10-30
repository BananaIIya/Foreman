import assert from 'node:assert/strict';
import {reviewCheckpoint} from './cursor_review.mjs';
const request={budget_usd:1,live_review_allowance_usd:.005,native_turn_limit:6,live_review_threshold:.65};
const base={request,spent:.7,result:{status:'finished',result:'public checkpoint'},answer:{status:'working'},index:1,
 state:{done:false,cost:.005,feedback:'',boundary:null}};
let calls=0;
const invoke=async p=>{calls++;assert.equal(p.public_checkpoint,'public checkpoint');return {review_cost_usd:.003,feedback:'grounded concern'};};
let result=await reviewCheckpoint({...base,invoke});
assert.equal(calls,1);assert.equal(result.done,true);assert.equal(result.cost,.003);assert.equal(result.feedback,'grounded concern');
result=await reviewCheckpoint({...base,state:result,invoke});assert.equal(calls,1);
for(const patch of [{spent:.3},{spent:.6},{result:{status:'finished'},answer:{status:'completed'}},{index:5},{state:{...base.state,boundary:'cost_boundary'}}]){
 await reviewCheckpoint({...base,...patch,invoke});assert.equal(calls,1);
}
result=await reviewCheckpoint({...base,invoke:async()=>({review_cost_usd:.5,feedback:'x'})});
assert.equal(result.boundary,'cost_boundary');assert.equal(result.cost,.5);
result=await reviewCheckpoint({...base,invoke:async()=>({review_cost_usd:null})});assert.equal(result.boundary,'unknown_review_usage');
console.log('Cursor live review checks passed: threshold, one call, no post-completion review, turn bound, existing boundary, actual overspend, unknown usage.');
