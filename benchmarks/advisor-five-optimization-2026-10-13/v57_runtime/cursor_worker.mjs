// One synchronous worker tool in the existing Cursor native conversation.
export function routineWorkerTool({request,invoke,spent,reviewCost,blocked,remoteBusy,stop}){
 const state={started:false,busy:false,cost:0,usageComplete:true};
 const tool={description:request.in_turn_worker_tool.description,
  inputSchema:request.in_turn_worker_tool.inputSchema,
  execute:async args=>{
   if(!request.in_turn_worker_enabled||state.started||blocked()||remoteBusy())
    return {status:'worker_unavailable',model_calls:0};
   const remaining=request.budget_usd-spent()-reviewCost();
   if(remaining<=request.in_turn_worker_continuation_reserve_usd)
    return {status:'insufficient_remaining_budget',model_calls:0};
   state.started=true;state.busy=true;
   try{
    const result=await invoke({arguments:args,remaining_before_worker_usd:remaining});
    const fee=result.actual_cost_usd;
    if(result.model_calls===0){state.cost=0;return result;}
    if(typeof fee!=='number'||!Number.isFinite(fee)||fee<0||result.usage_complete!==true){
     state.usageComplete=false;stop('unknown_delegated_worker_usage');
    }else{
     state.cost=fee;
     if(spent()+reviewCost()+fee>=request.budget_usd)stop('combined_worker_budget_exceeded');
    }
    return result;
   }catch(error){
    state.usageComplete=false;stop('unknown_delegated_worker_usage');throw error;
   }finally{state.busy=false;}
  }};
 return {tool,state};
}
