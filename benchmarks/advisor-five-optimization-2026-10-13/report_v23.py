"""Report a whole frozen cohort, retaining wrong and pending tasks."""
import json
from pathlib import Path
from trial_validity_v2 import result_for
ROOT=Path(__file__).resolve().parent
m=json.loads((ROOT/'manifest.json').read_text());rows=[]
for task in m['tasks']:
 solo=result_for('solo-'+task);hybrid=result_for('hybrid-v23-'+task)
 savings=1-hybrid['cost_usd']/solo['cost_usd'] if solo.get('cost_usd') and hybrid.get('cost_usd') is not None else None
 rows.append({'task':task,'solo':solo,'hybrid':hybrid,'savings_fraction':savings,'qualifies':solo['status']=='correct' and hybrid['status']=='correct' and savings is not None and savings>=.30})
complete=all(r['hybrid']['status'] in ['correct','wrong'] and r['hybrid'].get('cost_usd') is not None for r in rows)
total_solo=sum(r['solo']['cost_usd'] for r in rows)
total_hybrid=sum(r['hybrid']['cost_usd'] for r in rows) if complete else None
out={'candidate':'v23','complete':complete,'tasks':rows,'qualifying_tasks':sum(r['qualifies'] for r in rows),'correct_tasks':sum(r['hybrid']['status']=='correct' for r in rows),'solo_cost_usd':total_solo,'hybrid_cost_usd':total_hybrid,'aggregate_savings_fraction':1-total_hybrid/total_solo if complete else None,'goal_achieved':False,'final_audit_required':True,'cost_basis':m['cost_basis']}
(ROOT/'summary-v23.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({k:v for k,v in out.items() if k not in ('tasks','cost_basis')}))
for r in rows:print(json.dumps({'task':r['task'],'status':r['hybrid']['status'],'cost_usd':r['hybrid'].get('cost_usd'),'savings_fraction':r['savings_fraction'],'qualifies':r['qualifies']}))
