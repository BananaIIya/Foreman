"""Read-only completion audit. Does not run models or expose solutions."""
import hashlib,json,math
from pathlib import Path
from v9_runtime.metrics import aggregate_phases
from trial_validity_v2 import result_for
ROOT=Path(__file__).resolve().parent

def audit():
 manifest=json.loads((ROOT/'manifest.json').read_text())
 frozen=json.loads((ROOT/'raw/candidates/v40/manifest.json').read_text())
 problems=[];pending=[];rows=[]
 for relative,digest in frozen['source_sha256'].items():
  if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()!=digest:problems.append('Frozen runtime changed: '+relative)
 original={k:v for k,v in manifest['sources_sha256'].items() if k.startswith('tasks/')}
 for relative,digest in original.items():
  if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()!=digest:problems.append('Original task changed: '+relative)
 for task in manifest['tasks']:
  name='hybrid-v40-'+task;summary=result_for(name);solo=result_for('solo-'+task)
  row={'task':task,'status':summary['status'],'problems':[]}
  paths=list((ROOT/'raw/jobs'/name).glob('*/result.json'))
  if not paths:
   pending.append(task);rows.append(row);continue
  if len(paths)!=1:row['problems'].append('Not exactly one attempt');rows.append(row);continue
  phase_root=paths[0].parent/'agent';hybrid_path=phase_root/'hybrid-result.json'
  guard=ROOT/'raw'/(name+'-v40-runtime.json')
  if not guard.exists() or json.loads(guard.read_text())!=frozen:row['problems'].append('Per-attempt frozen runtime guard mismatch')
  if not hybrid_path.exists():row['problems'].append('Missing hybrid completion');rows.append(row);continue
  h=json.loads(hybrid_path.read_text());totals=aggregate_phases(phase_root/'phases')
  if h['advisor_calls'] > 2:row['problems'].append('Advisor call bound exceeded')
  if h['assignment']!=frozen['assignment'][task]:row['problems'].append('Model assignment changed')
  if not totals['complete'] or not h['usage_complete']:row['problems'].append('Missing native usage')
  if totals['cost_usd'] is not None and not math.isclose(totals['cost_usd'],h['cost_usd'],abs_tol=1e-10):row['problems'].append('Ledger and raw phase cost disagree')
  if h['verifier_feedback'] is not False:row['problems'].append('Verifier feedback not excluded')
  if h['advisor_workspace_access'] is not True:row['problems'].append('Native advisor remote access not recorded')
  phases=h['phases'];requests=list((phase_root/'phases').glob('*/request.json'))
  previous_progress=None
  for expert_phase in [p['phase'] for p in phases if p['role']=='advisor']:
   progress_path=phase_root/('expert-progress-'+expert_phase+'.json')
   request=json.loads((phase_root/'phases'/expert_phase/'request.json').read_text())
   prompt=json.loads(request['prompt'])
   if previous_progress is not None and prompt.get('expert_progress')!=previous_progress:row['problems'].append('Expert progress lost on continuation')
   if not progress_path.exists():
    row['problems'].append('Missing expert progress read receipt');continue
   progress=json.loads(progress_path.read_text())
   if progress.get('model_calls')!=0 or progress.get('grader_calls')!=0 or progress.get('correctness_proven') is not False:row['problems'].append('Expert progress incorrectly treated as a verdict')
   previous_progress=progress.get('report')
   if previous_progress is not None:
    from v34_runtime.progress import validate_progress
    contract=json.loads((phase_root/('progress-contract-'+expert_phase+'.json')).read_text())
    try:
     raw=(phase_root/('expert-progress-'+expert_phase+'.raw.json')).read_bytes()
     if hashlib.sha256(raw).hexdigest()!=progress['sha256']:row['problems'].append('Expert progress bytes changed')
     if validate_progress(raw,contract['token'],expert_phase)!=previous_progress:row['problems'].append('Expert progress receipt differs from raw report')
    except Exception:row['problems'].append('Invalid or stale expert progress report')
  for expert_phase in [p['phase'] for p in phases if p['role']=='advisor']:
   focus_path=phase_root/('corrective-focus-'+expert_phase+'.json')
   if not focus_path.exists():
    row['problems'].append('Missing corrective focus receipt: '+expert_phase);continue
   focus=json.loads(focus_path.read_text())
   if focus.get('extra_model_calls')!=0 or focus.get('verifier_feedback') is not False or focus.get('original_task_preserved') is not True or focus.get('success_claimed') is not False:row['problems'].append('Corrective focus boundary drift')
   if expert_phase=='advisor-0' and focus.get('priority_check') is not None:row['problems'].append('Correction priority applied to initial intervention')
   if focus.get('priority_check') is not None:
    import jsonschema
    from v39_runtime.policy import DECISION_SCHEMA
    try:jsonschema.validate(focus['priority_check'],DECISION_SCHEMA['properties']['priority_check'])
    except jsonschema.ValidationError:row['problems'].append('Invalid corrective focus schema')
    request_path=phase_root/'phases'/expert_phase/'request.json'
    request=json.loads(request_path.read_text())
    prompt=json.loads(request['prompt'])
    if prompt.get('corrective_focus')!=focus['priority_check']:row['problems'].append('Corrective focus missing from actual native request')
  for override_path in phase_root.glob('completion-review-*.json'):
   from v32_runtime.completion import unfinished_reason
   override=json.loads(override_path.read_text())
   source=override.get('expert_phase')
   if source not in ('advisor-0','advisor-1'):row['problems'].append('Invalid completion review source');continue
   native=json.loads((phase_root/'phases'/source/'phase-result.json').read_text())
   if unfinished_reason(native)!=override.get('reason'):row['problems'].append('Completion review contradicts native expert status')
   if override.get('original_decision',{}).get('action')!='deliver' or override.get('replacement_decision',{}).get('action')!='advisor':row['problems'].append('Completion override routing drift')
   if override.get('no_new_model_call') is not True or override.get('verifier_feedback') is not False:row['problems'].append('Completion review execution boundary drift')
  for promotion_path in phase_root.glob('advisor-scope-promotion-*.json'):
   promotion=json.loads(promotion_path.read_text())
   target=promotion.get('advisor_decision',{})
   if target.get('action')!='advisor' or 'original required artifacts' not in target.get('intervention',''):row['problems'].append('Advisor scope promotion failed to transfer full task')
   if promotion.get('no_new_model_call') is not True or promotion.get('verifier_feedback') is not False:row['problems'].append('Advisor scope promotion changed execution boundary')
   old=promotion.get('original_worker_decision',{}).get('intervention','')
   if old and old==target.get('intervention'):row['problems'].append('Worker-only contract copied unchanged to advisor')
  from v32_runtime.snapshots import verify_snapshot
  import tomllib
  declared_artifacts=tomllib.loads((ROOT/'tasks'/task/'task.toml').read_text()).get('artifacts',[])
  row['snapshot_warnings']=[]
  for expert_phase in [p['phase'] for p in phases if p['role']=='advisor']:
   snapshot_dir=phase_root/'artifact-snapshots'/expert_phase
   if not (snapshot_dir/'snapshot.json').exists():
    row['snapshot_warnings'].append('Snapshot unavailable: '+expert_phase);continue
   snapshot=json.loads((snapshot_dir/'snapshot.json').read_text())
   row['problems'].extend(verify_snapshot(snapshot_dir))
   if [f['source_path'] for f in snapshot['files']]!=list(dict.fromkeys(declared_artifacts)):row['problems'].append('Snapshot does not match original declared outputs')
   if snapshot['provenance'].get('task')!=task or snapshot['provenance'].get('advisor_phase')!=expert_phase:row['problems'].append('Snapshot phase identity mismatch')
   if not snapshot['complete']:row['snapshot_warnings'].append('Incomplete snapshot: '+expert_phase)
  if frozen['assignment'][task]['advisor']=='gpt-6.1-sol':
   expert_phases=[p['phase'] for p in phases if p['role']=='advisor']
   thread_ids=[]
   original_instruction=json.loads((phase_root/'phases/decision-plan/request.json').read_text())
   original_task=json.loads(original_instruction['prompt'])['original_task']
   for index,phase in enumerate(expert_phases):
    request=json.loads((phase_root/'phases'/phase/'request.json').read_text())
    payload=json.loads(request['prompt']); thread_ids.append(request['thread_id'])
    if payload.get('original_task')!=original_task:row['problems'].append('Fresh expert lost original task')
    if (phase_root/('resume-evidence-'+phase+'.json')).exists():row['problems'].append('Fresh expert received stale thread reference')
    base=json.loads((phase_root/'phases'/phase/'specialist-base-policy.json').read_text())
    if base.get('applied_on_start') is not True:row['problems'].append('Expert did not start fresh')
    if index:
     receipt_path=phase_root/('independent-context-'+phase+'.json')
     if not receipt_path.exists():row['problems'].append('Missing independent-context receipt');continue
     receipt=json.loads(receipt_path.read_text())
     if receipt.get('prior_thread_id')!=thread_ids[index-1] or receipt.get('new_thread_id')!=thread_ids[index]:row['problems'].append('Independent-context thread mismatch')
     if receipt.get('model_call_added') is not False or receipt.get('budget_reset') is not False or receipt.get('grader_feedback') is not False:row['problems'].append('Independent-context boundary drift')
     if not payload.get('independent_review'):row['problems'].append('Missing independent review contract')
     index_path=phase_root/('evidence-index-'+phase+'.json')
     if not index_path.exists():row['problems'].append('Missing evidence index receipt')
     else:
      index_receipt=json.loads(index_path.read_text())
      if index_receipt['status']=='available':
       from v38_runtime.evidence_index import public_commands, project
       from hybrid_native import read_events
       archive=(phase_root/index_receipt['local_archive']).read_bytes()
       bundle=json.loads(archive)
       if hashlib.sha256(archive).hexdigest()!=index_receipt['archive_sha256']:row['problems'].append('Evidence archive hash changed')
       if bundle.get('private_reasoning_included') is not False or bundle.get('grader_feedback') is not False:row['problems'].append('Nonpublic evidence archive')
       actual=public_commands(read_events(phase_root/'phases'/index_receipt['source_phase']/'app-server.events.jsonl'))
       if actual!=bundle['public_commands']:row['problems'].append('Archive command records disagree with native events')
       original_payload=dict(payload,worker_evidence=bundle['original_worker_evidence'])
       projected,expected_archive,expected_receipt=project(original_payload,actual,index_receipt['archive_path'])
       if projected!=payload or expected_archive!=archive:row['problems'].append('Evidence index cannot be reconstructed from original receipts')
       for key,value in expected_receipt.items():
        if index_receipt.get(key)!=value:row['problems'].append('Evidence index receipt mismatch: '+key)
       if index_receipt.get('uploaded') is not True:row['problems'].append('Evidence archive was not uploaded before reference')
      elif index_receipt.get('fallback')!='Original self-contained public evidence retained':row['problems'].append('Missing safe full-context fallback')
   if len(set(thread_ids))!=len(thread_ids):row['problems'].append('Corrective expert reused prior native context')
  prior_spent=0.0
  advisor_index=0
  for phase_record in phases:
   if phase_record['role']=='advisor':
    checkpoint_path=phase_root/('checkpoint-budget-'+phase_record['phase']+'.json')
    if not checkpoint_path.exists():row['problems'].append('Missing funded checkpoint budget journal')
    else:
     checkpoint=json.loads(checkpoint_path.read_text())
     available=max(0.,solo['cost_usd']*.72-prior_spent-min(.005,solo['cost_usd']*.03))
     allowance=available*.70 if advisor_index==0 else available
     if not math.isclose(checkpoint['phase_allowance_usd'],allowance,abs_tol=1e-10):row['problems'].append('Advisor checkpoint allowance mismatch')
     if not math.isclose(checkpoint['correction_reserve_usd'],available-allowance,abs_tol=1e-10):row['problems'].append('Correction reserve mismatch')
     request=json.loads((phase_root/'phases'/phase_record['phase']/'request.json').read_text())
     if request.get('budget_usd') is not None and not math.isclose(request['budget_usd'],allowance,abs_tol=1e-10):row['problems'].append('Native advisor cap differs from funded checkpoint')
    advisor_index+=1
   prior_spent+=phase_record['cost_usd'] or 0.
  advisor_seen=False
  for phase_record in phases:
   if phase_record['role']=='advisor':advisor_seen=True
   if not advisor_seen and phase_record['role']=='worker' and phase_record['phase'].startswith('worker-implementation-'):row['problems'].append('Unscoped worker implementation before advisor')
  if h['preparations']>1:row['problems'].append('Repeated preparation repair')
  plan_phase=phase_root/'phases/decision-plan/request.json'
  work_order=phase_root/'worker-work-order.json'
  if work_order.exists():
   import jsonschema
   from v32_runtime.policy import PLAN_SCHEMA
   try:jsonschema.validate(json.loads(work_order.read_text()),PLAN_SCHEMA)
   except jsonschema.ValidationError:row['problems'].append('Invalid component work contract')
  if not plan_phase.exists() or not work_order.exists():row['problems'].append('Missing pre-worker planning evidence')
  if phases[0]['role']!='decision' or phases[0]['phase']!='decision-plan':row['problems'].append('Planning cost not first in role ledger')
  worker_request=json.loads((phase_root/'phases/worker-discovery/request.json').read_text())
  if '"work_order":' not in worker_request.get('prompt','') or '"original_task":' in worker_request.get('prompt',''):row['problems'].append('Initial component worker scope drift')

  if {p.parent.name for p in requests}!={p['phase'] for p in phases if p['role']!='executor'}:row['problems'].append('A launched model phase is missing from ledger')
  executions=list((phase_root/'phases').glob('*/execution-result.json'))
  if {p.parent.name for p in executions}!={p['phase'] for p in phases if p['role']=='executor'}:row['problems'].append('Execution evidence and ledger mismatch')
  if len(executions)!=h['execution_only_integrations']:row['problems'].append('Execution count mismatch')
  for p in phases:
   if p['role']=='executor' and p['cost_usd']!=0:row['problems'].append('Unexpected model cost for execution-only phase')
  for p in executions:
   e=json.loads(p.read_text())
   if e.get('no_model_call') is not True or e['usage']['cost_usd']!=0 or p.with_name('request.json').exists():row['problems'].append('Execution-only phase misrepresented')
   if e['execution']['hidden_grader_used'] is not False:row['problems'].append('Executor used grader feedback')
   started=p.with_name('delivery-started.json')
   if not started.exists():row['problems'].append('Missing delivery execution journal')
   else:
    from v32_runtime.delivery import validate_manifest
    journal=json.loads(started.read_text())
    contracts=[json.loads(c.read_text()) for c in phase_root.glob('delivery-contract-advisor-*.json')]
    if journal['token'] not in [c['token'] for c in contracts]:row['problems'].append('Delivery not bound to an advisor intervention')
    try:validate_manifest(journal['manifest'],journal['token'])
    except ValueError:row['problems'].append('Invalid executed delivery manifest')
    if journal.get('scope')!='task' or journal.get('no_automatic_replay_of_uncertain_execution') is not True:row['problems'].append('Task-scoped delivery journal drift')
    for previous in journal.get('previous_confirmed_executions',[]):
     prior=p.parent.parent/previous/'execution-result.json'
     if not prior.exists():row['problems'].append('Repeated delivery after unknown prior execution')
     else:
      prior_result=json.loads(prior.read_text())
      if prior_result.get('status')!='completed' or prior_result.get('execution',{}).get('delivery_token')!=journal['token']:row['problems'].append('Repeated delivery not bound to confirmed task execution')
    if len({c['token'] for c in contracts})!=1 or any(c.get('scope')!='task' for c in contracts):row['problems'].append('Delivery task identity changed across interventions')
    expected_commands=journal['manifest']['commands']+journal['manifest']['check_commands']
    observed_commands=[c['command'] for c in e['execution']['checks']+e['execution']['unexecuted_commands']]
    if expected_commands!=observed_commands:row['problems'].append('Literal delivery commands differ from expert manifest')

  for req in requests:
   r=json.loads(req.read_text());role=req.parent.name.split('-')[0]
   delta_path=phase_root/('resume-evidence-'+req.parent.name+'.json')
   if delta_path.exists():
    from v32_runtime.context_delta import restore_evidence
    receipt=json.loads(delta_path.read_text());ref=receipt['reference']['same_native_thread_reference']
    if ref.get('source_phase') not in ('advisor-0','advisor-1'):row['problems'].append('Invalid prior native phase reference')
    else:
     prior=json.loads((phase_root/'phases'/ref['source_phase']/'phase-result.json').read_text())
     if ref.get('thread_id')!=prior.get('thread_id') or ref.get('thread_id')!=r.get('thread_id') or ref.get('turn_id')!=prior.get('turn_id'):row['problems'].append('Evidence reference crosses native thread or turn')
     known=dict(prior['evidence'],native_status=prior['status'],interrupted=prior.get('interrupted'))
     try:restore_evidence(json.loads(r['prompt'])['worker_evidence'],receipt,known)
     except (KeyError,ValueError,TypeError):row['problems'].append('Same-thread evidence cannot be reconstructed exactly')
   if role=='decision':
    reviewer_policy=req.with_name('reviewer-policy.json')
    if not reviewer_policy.exists():row['problems'].append('Missing reviewer capability policy')
    else:
     configuration=json.loads(reviewer_policy.read_text())
     if any(configuration.get('features',{}).get(key) is not False for key in ('apps','plugins','shell_tool','unified_exec','multi_agent')):row['problems'].append('Reviewer feature boundary drift')
    recovery_path=req.with_name('reviewer-recovery.json')
    if recovery_path.exists():
     recovery=json.loads(recovery_path.read_text());native=json.loads(req.with_name('phase-result.json').read_text())
     row.setdefault('reviewer_recoveries',[]).append(req.parent.name)
     if native.get('interrupted')!='decision_model_attempted_tool' or recovery.get('success_claimed') is not False:row['problems'].append('Misrepresented reviewer recovery')
     if recovery.get('original_native_cost_usd')!=native.get('usage',{}).get('cost_usd'):row['problems'].append('Reviewer recovery lost native cost')
     if recovery.get('decision',{}).get('action')!='advisor':row['problems'].append('Reviewer failure did not route to expert')
   if req.parent.name=='worker-discovery' and not math.isclose(r['budget_usd'],solo['cost_usd']*.20,abs_tol=1e-10):row['problems'].append('Initial worker budget drift')
   expected=frozen['assignment'][task][role]
   if role=='worker' and expected.startswith('cursor/'):expected=expected.removeprefix('cursor/')
   if role=='advisor' and expected.startswith('cursor/'):expected='grok-4.7'
   if r['model']!=expected:row['problems'].append('Native model mismatch: '+req.parent.name)
   if r.get('provider')=='cursor-sdk-bounded':
    phase=json.loads(req.with_name('phase-result.json').read_text())
    if (req.parent.name=='worker-discovery' or req.parent.name.startswith('worker-preparation-')) and r.get('native_call_limit')!=1:row['problems'].append('Component worker call limit drift')
    rounds=list(req.parent.glob('rounds/*/request.json'))
    if req.parent.name=='worker-discovery' and any(json.loads(x.read_text()).get('images') for x in rounds):row['problems'].append('Component worker received unrelated original image pixels')
    if req.parent.name=='worker-discovery' and r.get('command_limit')!=2:row['problems'].append('Component worker command limit drift')

    if len(rounds)!=len(phase['native_calls']) or len(rounds)>r['native_call_limit']:row['problems'].append('Bounded worker native call count mismatch')
    if phase['evidence']['command_count']>r['command_limit']:row['problems'].append('Worker exceeded command boundary')
    costs=[]
    for sub in rounds:
     native=json.loads(sub.with_name('sdk-result.json').read_text())
     measured=json.loads(sub.with_name('phase-result.json').read_text())
     params={p['id']:p['value'] for p in native.get('model',{}).get('params',[])}
     if native.get('model',{}).get('id')!='grok-4.7' or params.get('reasoning_effort')!=r['effort'] or params.get('context')!='256k' or params.get('fast')!='false':row['problems'].append('Bounded worker native model drift')
     if native['toolCalls']!=0:row['problems'].append('Bounded worker used direct native tools')
     costs.append(measured['usage']['cost_usd'])
    if any(c is None for c in costs):
     if phase['usage']['cost_usd'] is not None:row['problems'].append('Missing bounded worker usage concealed')
    elif not math.isclose(sum(costs),phase['usage']['cost_usd'],abs_tol=1e-10):row['problems'].append('Bounded worker native subcall costs disagree')
   if role=='advisor':
    if r.get('model')=='gpt-6.1-sol':
     from v32_runtime.native import SPECIALIST_BASE
     base_path=req.with_name('specialist-base-policy.json')
     if not base_path.exists():row['problems'].append('Missing native-default specialist policy receipt')
     else:
      base=json.loads(base_path.read_text())
      if base.get('strategy')!='provider_default_matching_verified_solo' or base.get('custom_base_instructions_supplied') is not False or base.get('model')!='gpt-6.1-sol' or base.get('effort')!='high':row['problems'].append('Native-default specialist policy mismatch')
    if r.get('provider')=='cursor-sdk-remote-tools':
     native=json.loads(req.with_name('sdk-result.json').read_text())
     if r.get('tools')!=['mcp'] or r.get('custom_tools')!=['remote_command'] or r.get('setting_sources')!=[] or r.get('mcp_servers')!={}:row['problems'].append('Cursor remote-only tool configuration drift')
     if len(native['turns'])>6 or native['commandCount']>12 or any(t['commandCount']>2 for t in native['turns']):row['problems'].append('Cursor advisor exceeded native command/turn bound')
     if any(e['name']!='mcp' for e in native['toolEvents']):row['problems'].append('Cursor advisor used a non-relay tool')
     command_file=req.with_name('remote-commands.json')
     commands=json.loads(command_file.read_text()) if command_file.exists() else []
     if len(commands)!=native['commandCount']:row['problems'].append('Native tool calls and remote command records disagree')
     params={p['id']:p['value'] for p in native['model']['params']}
     if params.get('reasoning_effort')!='high' or params.get('context')!='256k' or params.get('fast')!='false':row['problems'].append('Cursor model parameters drifted')
    else:
     if r.get('no_tools') or not r.get('remote_executor') or r.get('effort')!='high' or r.get('budget_usd') is None:row['problems'].append('Sol advisor not remote High with native budget')
     notice=req.with_name('budget-notice.json')
     if notice.exists():
      n=json.loads(notice.read_text())
      if n.get('repair_order')!='apply_supported_repairs_before_further_investigation':row['problems'].append('Missing supported-repair budget warning')
      if n.get('attempts')!=1 or n.get('threshold')!=.75 or n.get('hard_stop_unchanged') is not True:row['problems'].append('Budget notice policy drift')
     observations=req.with_name('observation-policy.json')
     o=json.loads(observations.read_text()) if observations.exists() else {}
     if o.get('tool_output_token_limit')!=2048:row['problems'].append('Missing bounded native observation policy')
     capabilities=req.with_name('native-capabilities.json')
     c=json.loads(capabilities.read_text()) if capabilities.exists() else {}
     if c.get('public_web')!='provider_default_matching_solo' or c.get('multi_agent') is not False or c.get('remote_environments')!=['daytona']:row['problems'].append('Sol public-web capability or remote scope mismatch')
  savings=1-h['cost_usd']/solo['cost_usd'] if h['cost_usd'] is not None and solo.get('cost_usd') else None
  row.update(cost_usd=h['cost_usd'],solo_cost_usd=solo.get('cost_usd'),solo_status=solo['status'],savings_fraction=savings,qualifies=summary['status']=='correct' and solo['status']=='correct' and savings is not None and savings>=.25,tests=summary.get('tests'),roles=totals['role_cost_usd'])
  if summary['status']=='invalid':row['problems'].append('Trial did not produce a valid original score')
  rows.append(row)
 problems += [r['task']+': '+p for r in rows for p in r['problems']]
 complete=not pending and len(rows)==5 and all(r.get('cost_usd') is not None for r in rows)
 total=sum(r['cost_usd'] for r in rows) if complete else None
 solo_total=sum(result_for('solo-'+t)['cost_usd'] for t in manifest['tasks'])
 saving=1-total/solo_total if total is not None else None
 count=sum(bool(r.get('qualifies')) for r in rows)
 out={'candidate':'v40','all_five_complete':complete,'pending':pending,'problems':problems,'original_files_checked':len(original),'qualifying_correct_and_cheaper':count,'hybrid_total_usd':total,'solo_total_usd':solo_total,'aggregate_savings_fraction':saving,'goal_evidence_passes':complete and not problems and count>=4 and saving>=.30,'rows':rows,'limitations':['One frozen attempt per task; does not establish repeat-run stability.','Standard native token price equivalents, not subscription invoice.','Development attempts are retained separately in expenses.json.','Infrastructure, outer engineering cost and unreported provider tool surcharges excluded.']}
 from acceptance_report import evaluate
 acceptance=evaluate('v40')
 out.update(acceptance_policy='acceptance-policy-20261014.json',successful_task_weighted_savings_fraction=acceptance['successful_task_weighted_savings_fraction'],goal_evidence_passes=not problems and acceptance['numerical_acceptance_pass'])
 (ROOT/'audit-v40.json').write_text(json.dumps(out,indent=2)+'\n')
 return out
if __name__=='__main__':
 out=audit();print(json.dumps({k:v for k,v in out.items() if k not in ('rows','limitations')}))
