"""Keep development and baseline spending visible, including failed attempts."""
import json
from pathlib import Path
from hybrid_native import account_codex,read_events
from routing import usage_cost
from v9_runtime.metrics import aggregate_phases
ROOT=Path(__file__).resolve().parent

def main():
 rows=[]
 for name in ['cursor-remote-tools','cursor-remote-tools-checkpoints','cursor-worker-native-image']:
  phases=ROOT/'raw/preflight'/name/'phases'
  if phases.exists():
   totals=aggregate_phases(phases)
   rows.append({'name':name,'category':'development_preflight','cost_usd':totals['cost_usd'],'known_cost_lower_bound_usd':totals['known_cost_lower_bound_usd'],'complete':(phases.parent/'probe.json').exists(),'result_path':str(phases.parent/'probe.json')})
 resume=ROOT/'raw/preflight/sol-advisor-resume/phases'
 if resume.exists():
  totals=aggregate_phases(resume)
  rows.append({'name':'sol-advisor-resume','category':'development_preflight','cost_usd':totals['cost_usd'],'known_cost_lower_bound_usd':totals['known_cost_lower_bound_usd'],'complete':(resume.parent/'probe.json').exists(),'result_path':str(resume.parent/'probe.json')})
 bounded=ROOT/'raw/preflight/cursor-bounded-worker/phases'
 if bounded.exists():
  totals=aggregate_phases(bounded)
  rows.append({'name':'cursor-bounded-worker','category':'development_preflight','cost_usd':totals['cost_usd'],'known_cost_lower_bound_usd':totals['known_cost_lower_bound_usd'],'complete':(bounded.parent/'probe.json').exists(),'result_path':str(bounded.parent/'probe.json')})
 sol=ROOT/'raw/preflight/sol-no-tools-patch/phase-result.json'
 if sol.exists():
  r=json.loads(sol.read_text());rows.append({'name':'sol-no-tools-patch','category':'development_preflight','cost_usd':r['usage']['cost_usd'],'complete':r['status']=='completed','result_path':str(sol)})
 sdk=ROOT/'raw/preflight/cursor-sdk-no-tools-probe/result.json'
 if sdk.exists():
  r=json.loads(sdk.read_text());rows.append({'name':'cursor-sdk-no-tools-probe','category':'development_preflight','cost_usd':usage_cost('grok-4.7',r['usage'],'cursor'),'complete':r['status']=='finished','result_path':str(sdk)})
 gate=ROOT/'raw/preflight/gate-native-probe/phase-result.json'
 if gate.exists():
  r=json.loads(gate.read_text());rows.append({'name':'gate-native-probe','category':'development_preflight','cost_usd':r['usage']['cost_usd'],'complete':r['status']=='completed','result_path':str(gate)})
 for job in sorted((ROOT/'raw/jobs').iterdir()):
  if not job.is_dir():continue
  trials=[p for p in job.iterdir() if p.is_dir() and (p/'config.json').exists()]
  for trial in trials:
   agent=trial/'agent';cost=None;known_lower_bound=0.0
   if (agent/'phases').exists():
    phase_totals=aggregate_phases(agent/'phases');cost=phase_totals['cost_usd'];known_lower_bound=phase_totals['known_cost_lower_bound_usd']
   elif (agent/'app-server.events.jsonl').exists():
    cost=account_codex(read_events(agent/'app-server.events.jsonl'),'gpt-6.1-sol')['cost_usd']
   elif (agent/'cursor-cli.txt').exists():
    ends=[e for e in read_events(agent/'cursor-cli.txt') if e.get('type')=='result']
    if len(ends)==1:cost=usage_cost('grok-4.7',ends[0].get('usage',{}),'cursor')
   path=trial/'result.json';r=json.loads(path.read_text()) if path.exists() else {}
   if cost is not None:known_lower_bound=cost
   if not path.exists():cost=None
   rows.append({'name':job.name,'trial':trial.name,'category':('advisor_solo_baseline' if job.name.startswith('solo-') else 'development_preflight' if job.name.startswith('transport-probe') else 'hybrid_attempt'),'cost_usd':cost,'known_cost_lower_bound_usd':known_lower_bound,'complete':path.exists(),'exception':r.get('exception_info'),'reward':(r.get('verifier_result') or {}).get('rewards',{}).get('reward'),'result_path':str(path)})
 totals={}
 for r in rows:
  t=totals.setdefault(r['category'],{'known_cost_usd':0.0,'unknown_or_pending_attempts':0,'attempts':0})
  t['attempts']+=1
  if r['cost_usd'] is None or not r['complete']:t['unknown_or_pending_attempts']+=1
  t['known_cost_usd']+=r.get('known_cost_lower_bound_usd',r['cost_usd'] or 0.0)
 out={'basis':'Standard native model token price equivalent, not subscription invoice. Infrastructure, engineering time and unreported tool surcharges excluded. Every attempt retained; pending cost is not zero.','totals':totals,'attempts':rows}
 (ROOT/'expenses.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(totals))
if __name__=='__main__':main()
