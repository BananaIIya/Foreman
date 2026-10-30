"""Apply the user-selected statistical acceptance policy (acceptance-policy-statistical-20261014.json).

Reads original grader results only; runs no models. Every run counts; unknown cost stays unknown.
"""
import json
from pathlib import Path
from trial_validity_v2 import result_for

ROOT = Path(__file__).resolve().parent


def evaluate(candidate='v60'):
    policy = json.loads((ROOT/'acceptance-policy-statistical-20261014.json').read_text())
    n = policy['runs_per_arm_per_task']
    rows = []
    for task in policy['tasks']:
        replaced = []
        def slot(name):
            # Per policy amendment: a run that failed before any model call is replaced once under name+'b'.
            r = result_for(name)
            if r['status'] == 'invalid' and (ROOT/'raw/jobs'/(name+'b')).exists():
                replaced.append({'original': name, 'original_status': r['status'], 'replacement': name+'b'})
                r = result_for(name+'b')
            return r
        solo = [slot('solo-'+task)] + [slot(f'solo-{task}-r{r}') for r in range(2, n+1)]
        hyb = [slot(f'hybrid-{candidate}-{task}-r{r}') for r in range(1, n+1)]
        def summarize(runs):
            finished = [r for r in runs if r['status'] in ('correct', 'wrong', 'invalid')]
            costs = [r.get('cost_usd') for r in runs]
            known = all(isinstance(c, (int, float)) for c in costs)
            return {'runs': len(runs), 'finished': len(finished),
                    'correct': sum(r['status'] == 'correct' for r in runs),
                    'statuses': [r['status'] for r in runs],
                    'costs_usd': costs,
                    'mean_cost_usd': sum(costs)/len(costs) if known and costs else None,
                    'cost_complete': known}
        s, h = summarize(solo), summarize(hyb)
        complete = s['finished'] == n and h['finished'] == n
        ratio = h['mean_cost_usd']/s['mean_cost_usd'] if s['mean_cost_usd'] and h['mean_cost_usd'] is not None else None
        rows.append({'task': task, 'solo': s, 'hybrid': h, 'complete': complete, 'replaced_pre_model_runs': replaced,
                     'accuracy_ok': complete and h['correct'] >= s['correct'],
                     'mean_cost_ratio': ratio,
                     'cost_ok': ratio is not None and ratio <= .75 + 1e-12})
    all_complete = all(r['complete'] for r in rows)
    costs_known = all(r['solo']['mean_cost_usd'] is not None and r['hybrid']['mean_cost_usd'] is not None for r in rows)
    agg = (sum(r['hybrid']['mean_cost_usd'] for r in rows)/sum(r['solo']['mean_cost_usd'] for r in rows)) if costs_known else None
    passing = sum(r['accuracy_ok'] and r['cost_ok'] for r in rows)
    out = {'candidate': candidate, 'policy': policy, 'rows': rows, 'all_runs_complete': all_complete,
           'tasks_passing': passing, 'aggregate_mean_cost_ratio': agg,
           'aggregate_savings_fraction': 1-agg if agg is not None else None,
           'accept': all_complete and passing == len(rows) and agg is not None and agg <= .70 + 1e-12,
           'cost_basis': 'Native token standard-rate equivalent, not invoice; infrastructure excluded'}
    (ROOT/f'stats-acceptance-{candidate}.json').write_text(json.dumps(out, indent=2)+'\n')
    return out


if __name__ == '__main__':
    o = evaluate()
    for r in o['rows']:
        print(f"{r['task']:32s} solo {r['solo']['correct']}/{r['solo']['finished']} mean ${r['solo']['mean_cost_usd']}  |  "
              f"hybrid {r['hybrid']['correct']}/{r['hybrid']['finished']} mean ${r['hybrid']['mean_cost_usd']}  "
              f"ratio {r['mean_cost_ratio']}  acc_ok {r['accuracy_ok']} cost_ok {r['cost_ok']}")
    print({k: o[k] for k in ('all_runs_complete', 'tasks_passing', 'aggregate_savings_fraction', 'accept')})
