"""Read-only completion audit. Does not run models or expose solutions."""
import hashlib,json,math
from pathlib import Path
from metrics import aggregate_phases
from report import result_for
ROOT=Path(__file__).resolve().parent

def audit():
 manifest=json.loads((ROOT/'manifest.json').read_text())
 frozen=json.loads((ROOT/'raw/candidates/v4/manifest.json').read_text())
 problems=[];pending=[];rows=[]
 for relative,digest in frozen['source_sha256'].items():
  if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()!=digest:problems.append('Frozen runtime changed: '+relative)
 original={k:v for k,v in manifest['sources_sha256'].items() if k.startswith('tasks/')}
 for relative,digest in original.items():
  if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()!=digest:problems.append('Original task changed: '+relative)
 for task in manifest['tasks']:
  name='hybrid-v4-'+task;summary=result_for(name);solo=result_for('solo-'+task)
  row={'task':task,'status':summary['status'],'problems':[]}
  paths=list((ROOT/'raw/jobs'/name).glob('*/result.json'))
  if not paths:
   pending.append(task);rows.append(row);continue
  if len(paths)!=1:row['problems'].append('Not exactly one attempt');rows.append(row);continue
  phase_root=paths[0].parent/'agent';hybrid_path=phase_root/'hybrid-result.json'
  guard=ROOT/'raw'/(name+'-v4-runtime.json')
  if not guard.exists() or json.loads(guard.read_text())!=frozen:row['problems'].append('Per-attempt frozen runtime guard mismatch')
  if not hybrid_path.exists():row['problems'].append('Missing hybrid completion');rows.append(row);continue
  h=json.loads(hybrid_path.read_text());totals=aggregate_phases(phase_root/'phases')
  if h['assignment']!=frozen['assignment'][task]:row['problems'].append('Model assignment changed')
  if not totals['complete'] or not h['usage_complete']:row['problems'].append('Missing native usage')
  if totals['cost_usd'] is not None and not math.isclose(totals['cost_usd'],h['cost_usd'],abs_tol=1e-10):row['problems'].append('Ledger and raw phase cost disagree')
  if h['verifier_feedback'] is not False:row['problems'].append('Verifier feedback not excluded')
  if h['advisor_workspace_access'] is not False:row['problems'].append('Advisor tool isolation not enforced')
  phases=h['phases'];requests=list((phase_root/'phases').glob('*/request.json'))
  if {p.parent.name for p in requests}!={p['phase'] for p in phases}:row['problems'].append('A launched phase is missing from ledger')
  for req in requests:
   r=json.loads(req.read_text());role=req.parent.name.split('-')[0]
   expected=frozen['assignment'][task][role]
   if role=='worker' and expected.startswith('cursor/'):expected=expected.removeprefix('cursor/')
   if role=='advisor' and expected.startswith('cursor/'):expected='grok-4.7'
   if r['model']!=expected:row['problems'].append('Native model mismatch: '+req.parent.name)
   if role=='advisor':
    if r.get('provider')=='cursor-sdk':
     native=json.loads(req.with_name('sdk-result.json').read_text())
     if native['toolCalls']!=0:row['problems'].append('Advisor used a tool')
     params={p['id']:p['value'] for p in native['model']['params']}
     if params.get('reasoning_effort')!='high' or params.get('context')!='256k' or params.get('fast')!='false':row['problems'].append('Cursor model parameters drifted')
    elif not r.get('no_tools') or r.get('effort')!='high':row['problems'].append('Sol advisor not isolated High')
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
 out={'candidate':'v4','all_five_complete':complete,'pending':pending,'problems':problems,'original_files_checked':len(original),'qualifying_correct_and_cheaper':count,'hybrid_total_usd':total,'solo_total_usd':solo_total,'aggregate_savings_fraction':saving,'goal_evidence_passes':complete and not problems and count>=4 and saving>=.30,'rows':rows,'limitations':['One frozen attempt per task; does not establish repeat-run stability.','Standard native token price equivalents, not subscription invoice.','Development attempts are retained separately in expenses.json.','Infrastructure, outer engineering cost and unreported provider tool surcharges excluded.']}
 (ROOT/'audit-v4.json').write_text(json.dumps(out,indent=2)+'\n')
 return out
if __name__=='__main__':
 out=audit();print(json.dumps({k:v for k,v in out.items() if k not in ('rows','limitations')}))
