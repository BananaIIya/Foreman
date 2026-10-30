/** One review at an existing native Cursor turn boundary; no SDK restart. */
export async function reviewCheckpoint({request, spent, result, answer, index, state, invoke}) {
  if (state.done || state.boundary || result.status !== 'finished' || answer?.status !== 'working'
      || !(request.live_review_allowance_usd > 0) || spent < request.budget_usd * .5
      || index + 1 >= request.native_turn_limit) return state;
  const reply = await invoke({spent_usd:spent,
    remaining_usd:Math.max(0,request.budget_usd-state.cost-spent),public_checkpoint:result.result??''});
  const cost = reply.review_cost_usd;
  if (typeof cost !== 'number' || !Number.isFinite(cost) || cost < 0)
    return {...state, done:true, boundary:'unknown_review_usage'};
  return {...state, done:true, cost, feedback:reply.feedback??'',
    boundary:spent >= Math.max(0,request.budget_usd-cost) ? 'cost_boundary' : null};
}
