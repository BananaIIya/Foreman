"""Read-only comparable results and costs; never runs the hidden verifier."""
import json
from pathlib import Path
from hybrid_native import account_codex, read_events
from routing import usage_cost

ROOT = Path(__file__).resolve().parent


def result_for(name):
    paths = list((ROOT / 'raw/jobs' / name).glob('*/result.json'))
    if not paths:
        return {'status':'pending_or_running'}
    assert len(paths) == 1
    path = paths[0]; result = json.loads(path.read_text()); logs = path.parent / 'agent'
    ctrf = path.parent / 'verifier/ctrf.json'
    checks = json.loads(ctrf.read_text())['results']['summary'] if ctrf.exists() else None
    reward = (result.get('verifier_result') or {}).get('rewards', {}).get('reward')
    valid = result.get('exception_info') is None and reward in (0, 1) and checks is not None
    roles = None
    if (logs / 'hybrid-result.json').exists():
        hybrid = json.loads((logs / 'hybrid-result.json').read_text())
        cost = hybrid['cost_usd'] if hybrid['usage_complete'] else None
        roles = {r:sum(x['cost_usd'] or 0 for x in hybrid['phases'] if x['role']==r)
                 for r in ['worker','decision','advisor']} if hybrid['usage_complete'] else None
        valid = valid and hybrid['verifier_feedback'] is False
        model = hybrid['assignment']; terminal = hybrid['terminal_reason']
    elif (logs / 'app-server.events.jsonl').exists():
        events = read_events(logs / 'app-server.events.jsonl')
        cost = account_codex(events, 'gpt-6.1-sol')['cost_usd']
        complete = [e for e in events if e.get('method') == 'turn/completed']
        valid = valid and bool(complete) and complete[-1]['params']['turn']['status'] == 'completed'
        model = 'gpt-6.1-sol high'; terminal = 'native_completed' if complete else 'missing_completion'
    else:
        events = read_events(logs / 'cursor-cli.txt')
        complete = [e for e in events if e.get('type') == 'result']
        inits = [e for e in events if e.get('type') == 'system' and e.get('subtype') == 'init']
        valid = valid and len(complete)==1 and complete[0].get('is_error') is False
        cost = usage_cost('grok-4.7', complete[0].get('usage', {}), 'cursor') if len(complete)==1 else None
        model = inits[0].get('model') if inits else None; terminal = 'native_completed' if complete else 'missing_completion'
    return {'status':('correct' if reward==1 else 'wrong') if valid else 'invalid',
            'reward': reward if valid else None, 'tests': checks, 'cost_usd':cost,
            'role_cost_usd':roles, 'model':model, 'terminal_reason':terminal,
            'exception':result.get('exception_info'), 'result_path':str(path)}


def main():
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    rows=[]
    for task in manifest['tasks']:
        solo=result_for('solo-'+task); hybrid=result_for('hybrid-v1-'+task)
        savings=None
        if solo.get('cost_usd') and hybrid.get('cost_usd') is not None:
            savings=1-hybrid['cost_usd']/solo['cost_usd']
        qualifies=(solo.get('status')=='correct' and hybrid.get('status')=='correct'
                   and savings is not None and savings>=manifest['minimum_savings_fraction'])
        rows.append({'task':task,'solo':solo,'hybrid_v1':hybrid,'savings_fraction':savings,'qualifies':qualifies})
    summary={'candidate':'v1','tasks':rows,'qualifying_tasks':sum(x['qualifies'] for x in rows),
             'goal_achieved':False,'note':'Goal remains unproven until all five final trials, comparable costs and completion audit are complete.'}
    (ROOT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({'qualifying_tasks':summary['qualifying_tasks'],'tasks':[
        {'task':x['task'],'solo':x['solo']['status'],'solo_cost':x['solo'].get('cost_usd'),
         'hybrid':x['hybrid_v1']['status'],'hybrid_cost':x['hybrid_v1'].get('cost_usd')} for x in rows]}))


if __name__=='__main__':
    main()
