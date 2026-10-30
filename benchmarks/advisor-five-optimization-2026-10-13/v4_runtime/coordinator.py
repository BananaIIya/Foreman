"""Pure asynchronous control flow. No task solution or hidden verifier access."""
import json
from .policy import Budget,DISCOVER,PREPARE,WORKER,route,evidence_fingerprint,validate_patch

class Coordinator:
 def __init__(self,baseline,worker_model,worker,decision,advisor,apply_patch,emit):
  self.budget=Budget(baseline);self.worker_model=worker_model
  self.worker=worker;self.decision=decision;self.advisor=advisor;self.apply_patch=apply_patch;self.emit=emit
  self.records=[];self.history=[];self.integrations=0
 def record(self,role,phase,result):
  self.budget.record(result['usage']['cost_usd'])
  self.records.append({'role':role,'phase':phase,'cost_usd':result['usage']['cost_usd'],'status':result['status'],'spent_usd':self.budget.spent})
  self.emit('ledger',self.records)
 async def work(self,phase,payload,policy,cap):
  result=await self.worker(phase,payload,policy,cap);self.record('worker',phase,result);return result
 async def run(self,instruction):
  b=self.budget
  result=await self.work('worker-discovery',{'original_task':instruction},DISCOVER,b.ceiling)
  b.worker_phase_floor=result['usage']['cost_usd'] or 0.0
  stage='discovery';terminal='maximum_checkpoints_reached'
  for cycle in range(6):
   if not b.can_continue():terminal='budget_exhausted';break
   context={'stage':stage,'original_task':instruction,'worker_evidence':result['evidence'],
    'preparations_used':b.preparations,'self_checks_used':b.self_checks,'advisor_calls_used':b.advisor_calls,
    'remaining_cost_target_usd':b.ceiling-b.spent,'previous_interventions':[r for r in self.records if r['role']=='advisor']}
   gate=await self.decision('decision-'+str(cycle),context);self.record('decision','decision-'+str(cycle),gate)
   decision=gate['decision'];action=route(decision,stage,b,self.history,self.worker_model)
   self.emit('decision-'+str(cycle),{'decision':decision,'action':action,'advisor_allowance_usd':b.advisor_allowance()})
   if action in ('deliver','budget_exhausted','repeated_issue'):terminal=action;break
   if action=='prepare':
    b.preparations+=1
    result=await self.work('worker-preparation-'+str(cycle),{'original_task':instruction,'preparation_contract':decision},PREPARE,b.preparation_allowance(self.worker_model))
    stage='preparation';continue
   if action=='advisor':
    phase='advisor-'+str(b.advisor_calls)
    contract=dict(decision,action='advisor')
    if decision['action']!='advisor':
     contract['deferred_worker_action']=decision['action']
     contract['deferred_worker_step']=decision['intervention']
     contract['deferred_worker_acceptance']=decision['acceptance']
     contract['intervention']='Resolve the critical issue in this contract directly from the original task and available evidence. Provide the smallest complete executable correction for the difficult part. Routine preparation and execution remain worker responsibilities. Request additional evidence only if essential to avoid fabrication.'
     contract['acceptance']='The worker can execute the correction and validate it against the original requirements using supplied commands. Do not claim unexecuted checks passed.'
    advice=await self.advisor(phase,{'original_task':instruction,'contract':contract,'worker_evidence':result['evidence'],'cost_target_usd':b.advisor_allowance()})
    self.record('advisor',phase,advice);b.advisor_calls+=1;self.history.append(evidence_fingerprint(decision))
    patch=advice.get('patch');receipt={'applied':False}
    try:
     validate_patch(patch)
     if patch['status']=='patch':receipt=await self.apply_patch(patch)
    except (ValueError,TypeError,KeyError) as exc:
     receipt={'applied':False,'protocol_error':str(exc)}
    except Exception as exc:
     # Model schema errors are recorded and handed to worker, not mislabeled infrastructure.
     import jsonschema
     if not isinstance(exc,jsonschema.ValidationError):raise
     receipt={'applied':False,'protocol_error':str(exc).split('\n')[0]}
    was_over_budget=not b.can_continue()
    handoff={'original_task':instruction,'specialist_status':advice['status'],'patch_receipt':receipt,
     'specialist_handoff':{k:v for k,v in patch.items() if k!='files'} if isinstance(patch,dict) else {},
     'actual_file_paths':receipt.get('paths',[]),
     'raw_advice_if_unapplied':advice['evidence']['text'] if not receipt.get('applied') else None,
     'last_integration_opportunity':was_over_budget}
    # Deliberately NO budget-exhausted branch here: reproduce/fix the v1 failure.
    result=await self.work('worker-integration-'+str(cycle),handoff,WORKER,b.integration_cap(self.worker_model));self.integrations+=1
    if was_over_budget or not b.can_continue():terminal='budget_exhausted_after_integration';break
    stage='delivery';continue
   if action=='self_check':b.self_checks+=1
   result=await self.work('worker-implementation-'+str(cycle),{'original_task':instruction,'checkpoint':decision},WORKER,max(0,b.ceiling-b.spent))
   stage='delivery'
  return {'terminal_reason':terminal,'cost_usd':b.spent if b.usage_complete else None,'known_cost_lower_bound_usd':b.spent,
   'usage_complete':b.usage_complete,'baseline_cost_usd':b.baseline,'target_ceiling_usd':b.ceiling,
   'advisor_calls':b.advisor_calls,'preparations':b.preparations,'integrations':self.integrations,'self_checks':b.self_checks,'phases':self.records,
   'verifier_feedback':False,'advisor_workspace_access':False,'advisor_code_applied_by_controller':True}
