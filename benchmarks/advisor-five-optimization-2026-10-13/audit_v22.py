"""Read-only completion audit. Does not run models or expose solutions."""
import hashlib,json,math
from pathlib import Path
from v9_runtime.metrics import aggregate_phases
from trial_validity_v2 import result_for
ROOT=Path(__file__).resolve().parent

def audit():
 manifest=json.loads((ROOT/'manifest.json').read_text())
 frozen=json.loads((ROOT/'raw/candidates/v22/manifest.json').read_text())
 problems=[];pending=[];rows=[]
 for relative,digest in frozen['source_sha256'].items():
  if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()!=digest:problems.append('Frozen runtime changed: '+relative)
 original={k:v for k,v in manifest['sources_sha256'].items() if k.startswith('tasks/')}
 for relative,digest in original.items():
  if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()!=digest:problems.append('Original task changed: '+relative)
 for task in manifest['tasks']:
  name='hybrid-v22-'+task;summary=result_for(name);solo=result_for('solo-'+task)
  row={'task':task,'status':summary['status'],'problems':[]}
  paths=list((ROOT/'raw/jobs'/name).glob('*/result.json'))
  if not paths:
   pending.append(task);rows.append(row);continue
  if len(paths)!=1:row['problems'].append('Not exactly one attempt');rows.append(row);continue
  phase_root=paths[0].parent/'agent';hybrid_path=phase_root/'hybrid-result.json'
  guard=ROOT/'raw'/(name+'-v22-runtime.json')
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
  advisor_seen=False
  for phase_record in phases:
   if phase_record['role']=='advisor':advisor_seen=True
   if not advisor_seen and phase_record['role']=='worker' and phase_record['phase'].startswith('worker-implementation-'):row['problems'].append('Unscoped worker implementation before advisor')
  if h['preparations']>1:row['problems'].append('Repeated preparation repair')
  plan_phase=phase_root/'phases/decision-plan/request.json'
  work_order=phase_root/'worker-work-order.json'
  if not plan_phase.exists() or not work_order.exists():row['problems'].append('Missing pre-worker planning evidence')
  if phases[0]['role']!='decision' or phases[0]['phase']!='decision-plan':row['problems'].append('Planning cost not first in role ledger')
  worker_request=json.loads((phase_root/'phases/worker-discovery/request.json').read_text())
  if '"work_order":' not in worker_request.get('prompt','') or '"original_task":' in worker_request.get('prompt',''):row['problems'].append('Initial worker not scoped to work order')

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
    from v22_runtime.delivery import validate_manifest
    journal=json.loads(started.read_text())
    contracts=[json.loads(c.read_text()) for c in phase_root.glob('delivery-contract-advisor-*.json')]
    if journal['token'] not in [c['token'] for c in contracts]:row['problems'].append('Delivery not bound to an advisor intervention')
    try:validate_manifest(journal['manifest'],journal['token'])
    except ValueError:row['problems'].append('Invalid executed delivery manifest')
    expected_commands=journal['manifest']['commands']+journal['manifest']['check_commands']
    observed_commands=[c['command'] for c in e['execution']['checks']+e['execution']['unexecuted_commands']]
    if expected_commands!=observed_commands:row['problems'].append('Literal delivery commands differ from expert manifest')

  for req in requests:
   r=json.loads(req.read_text());role=req.parent.name.split('-')[0]
   if req.parent.name=='worker-discovery' and not math.isclose(r['budget_usd'],solo['cost_usd']*.20,abs_tol=1e-10):row['problems'].append('Initial worker budget drift')
   expected=frozen['assignment'][task][role]
   if role=='worker' and expected.startswith('cursor/'):expected=expected.removeprefix('cursor/')
   if role=='advisor' and expected.startswith('cursor/'):expected='grok-4.7'
   if r['model']!=expected:row['problems'].append('Native model mismatch: '+req.parent.name)
   if r.get('provider')=='cursor-sdk-bounded':
    phase=json.loads(req.with_name('phase-result.json').read_text())
    if (req.parent.name=='worker-discovery' or req.parent.name.startswith('worker-preparation-')) and r.get('native_call_limit')!=1:row['problems'].append('Initial worker report-call removal drift')
    rounds=list(req.parent.glob('rounds/*/request.json'))
    if req.parent.name=='worker-discovery' and any(json.loads(x.read_text()).get('images') for x in rounds):row['problems'].append('Mechanical-preparation worker received image pixels')

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
      if n.get('attempts')!=1 or n.get('threshold')!=.75 or n.get('hard_stop_unchanged') is not True:row['problems'].append('Budget notice policy drift')
     observations=req.with_name('observation-policy.json')
     o=json.loads(observations.read_text()) if observations.exists() else {}
     if o.get('tool_output_token_limit')!=2048:row['problems'].append('Missing bounded native observation policy')
     capabilities=req.with_name('native-capabilities.json')
     c=json.loads(capabilities.read_text()) if capabilities.exists() else {}
     if c.get('public_web')!='provider_default_matching_solo' or c.get('multi_agent') is not False or c.get('remote_environments')!=['daytona']:row['problems'].append('Sol public-web capability or remote scope mismatch')
  savings=1-h['cost_usd']/solo['cost_usd'] if h['cost_usd'] is not None and solo.get('cost_usd') else None
  row.update(cost_usd=h['cost_usd'],solo_cost_usd=solo.get('cost_usd'),solo_status=solo['status'],savings_fraction=savings,qualifies=summary['status']=='correct' and solo['status']=='correct' and savings is not None and savings>=.30,tests=summary.get('tests'),roles=totals['role_cost_usd'])
  if summary['status']=='invalid':row['problems'].append('Trial did not produce a valid original score')
  rows.append(row)
 problems += [r['task']+': '+p for r in rows for p in r['problems']]
 complete=not pending and len(rows)==5 and all(r.get('cost_usd') is not None for r in rows)
 total=sum(r['cost_usd'] for r in rows) if complete else None
 solo_total=sum(result_for('solo-'+t)['cost_usd'] for t in manifest['tasks'])
 saving=1-total/solo_total if total is not None else None
 count=sum(bool(r.get('qualifies')) for r in rows)
 out={'candidate':'v22','all_five_complete':complete,'pending':pending,'problems':problems,'original_files_checked':len(original),'qualifying_correct_and_cheaper':count,'hybrid_total_usd':total,'solo_total_usd':solo_total,'aggregate_savings_fraction':saving,'goal_evidence_passes':complete and not problems and count>=4 and saving>=.30,'rows':rows,'limitations':['One frozen attempt per task; does not establish repeat-run stability.','Standard native token price equivalents, not subscription invoice.','Development attempts are retained separately in expenses.json.','Infrastructure, outer engineering cost and unreported provider tool surcharges excluded.']}
 (ROOT/'audit-v22.json').write_text(json.dumps(out,indent=2)+'\n')
 return out
if __name__=='__main__':
 out=audit();print(json.dumps({k:v for k,v in out.items() if k not in ('rows','limitations')}))
