"""Read-only live metadata. Never prints model thinking, task outputs or secrets."""
import argparse,json,time
from pathlib import Path
from hybrid_native import read_events,account_codex
from routing import usage_cost

ROOT=Path(__file__).resolve().parent

def phase_progress(phase):
    request=phase/'request.json';result=phase/'phase-result.json'
    if not request.exists():return None
    req=json.loads(request.read_text())
    if result.exists():
        value=json.loads(result.read_text())
        return {'phase':phase.name,'status':value['status'],'cost_usd':value['usage']['cost_usd'],
                'known_cost_lower_bound_usd':value['usage'].get('known_cost_lower_bound_usd',value['usage']['cost_usd']),
                'commands':value.get('evidence',{}).get('command_count'),'interrupted':value.get('interrupted')}
    event=phase/'app-server.events.jsonl'
    if event.exists():
        events=read_events(event)
        starts=[e['params']['turn']['id'] for e in events if e.get('method')=='turn/started']
        usage=account_codex(events,req['model'],starts[-1]) if starts else {'cost_usd':None,'responses':0}
        items=[e['params']['item'] for e in events if e.get('method')=='item/completed']
        return {'phase':phase.name,'status':'running','known_native_cost_usd':usage['cost_usd'],
                'native_responses':usage['responses'],
                'completed_commands':sum(i['type']=='commandExecution' for i in items),
                'seconds_since_event':round(time.time()-event.stat().st_mtime,1)}
    results=list(phase.glob('rounds/*/phase-result.json'))
    known=sum((json.loads(p.read_text())['usage']['cost_usd'] or 0) for p in results)
    if req.get('provider')=='cursor-sdk-remote-tools':
        results=list(phase.glob('turns/*/sdk-result.json'))
        known=sum((usage_cost('grok-4.7',json.loads(p.read_text()).get('usage') or {},'cursor') or 0) for p in results)
    return {'phase':phase.name,'status':'running','known_completed_subcall_cost_usd':known,
            'completed_subcalls':len(results),'pending_cost_is_not_zero':True}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('candidate');args=parser.parse_args()
    for job in sorted((ROOT/'raw/jobs').glob('hybrid-'+args.candidate+'-*')):
        for trial in job.iterdir():
            if not trial.is_dir():continue
            phases=[phase_progress(p) for p in sorted((trial/'agent/phases').glob('*')) if p.is_dir()]
            print(json.dumps({'job':job.name,'trial':trial.name,'final_result_exists':(trial/'result.json').exists(),
                'phases':[p for p in phases if p is not None]}))

if __name__=='__main__':main()
