"""V62 official advisor pattern: Luna+advisor vs Luna alone (primary), and vs advisor solo (secondary).

Reads original grader results and each run's v62-usage.json. Runs no models. n is small (3 per arm).
"""
import json
from pathlib import Path
from trial_validity_v2 import result_for

ROOT = Path(__file__).resolve().parent
TASKS = ['cad-model', 'hof-topology-interpenetration', 'satb-audio-transcription', 'wal-recovery-ordering', 'atrx-vep-crispr']
PROBE_COST = 'raw/preflight/v62-protocol-probe/result.json'


def run_row(name):
    res = result_for(name)
    paths = list((ROOT/'raw/jobs'/name).glob('*/result.json'))
    row = {'run': name, 'status': res['status'], 'tests': res.get('tests'), 'cost_usd': None}
    if not paths:
        return row
    usage = paths[0].parent/'agent'/'v62-usage.json'
    if usage.exists():
        u = json.loads(usage.read_text())
        row.update(cost_usd=u['total_cost_usd'], executor_cost_usd=u['executor']['cost_usd'],
                   usage_complete=u['usage_complete'])
        if u.get('advisor'):
            a = u['advisor']
            # A failed advisor call (e.g. provider transport drop) was still billed; recover its native usage.
            adv_cost, recovered = 0., []
            for c in a['calls']:
                cost = c.get('cost_usd')
                if cost is None:
                    pr = paths[0].parent/'agent'/'advisor'/f"call-{c['call']}"/'phase-result.json'
                    cost = json.loads(pr.read_text())['usage'].get('cost_usd') if pr.exists() else None
                    recovered.append({'call': c['call'], 'cost_usd': cost})
                adv_cost = None if (adv_cost is None or cost is None) else adv_cost + cost
            if a['cost_usd'] is None and adv_cost is not None and row.get('executor_cost_usd') is not None:
                row.update(cost_usd=row['executor_cost_usd'] + adv_cost, usage_complete=True, recovered_failed_call_costs=recovered)
            a = dict(a, cost_usd=adv_cost)
            row.update(advisor_cost_usd=a['cost_usd'], advisor_calls=sum(1 for c in a['calls'] if c.get('ok')),
                       advisor_failed_calls=sum(1 for c in a['calls'] if not c.get('ok')),
                       nudged=a['final_review_nudge_sent'])
    return row


def arm(rows):
    done = [r for r in rows if r['status'] in ('correct', 'wrong', 'invalid')]
    costs = [r['cost_usd'] for r in done]
    known = bool(costs) and all(isinstance(c, (int, float)) for c in costs)
    out = {'finished': len(done), 'correct': sum(r['status'] == 'correct' for r in done),
           'mean_cost_usd': sum(costs)/len(costs) if known else None,
           'mean_tests_passed_fraction': (sum(r['tests']['passed']/r['tests']['tests'] for r in done if r.get('tests'))
                                          / max(1, sum(1 for r in done if r.get('tests')))) if done else None,
           'runs': rows}
    adv = [r for r in done if 'advisor_calls' in r]
    if adv:
        out['mean_advisor_calls'] = sum(r['advisor_calls'] for r in adv)/len(adv)
        out['nudges'] = sum(bool(r.get('nudged')) for r in adv)
        ac = [r.get('advisor_cost_usd') for r in adv]
        if all(isinstance(c, (int, float)) for c in ac) and known and sum(costs):
            out['advisor_cost_share'] = sum(ac)/sum(costs)
    return out


def advisor_solo(task):
    names = ['solo-'+task] + [f'solo-{task}-r{i}' for i in (2, 3)]
    rows = []
    for n in names:
        r = result_for(n)
        if r['status'] == 'invalid' and (ROOT/'raw/jobs'/(n+'b')).exists():
            r = result_for(n+'b')
        if r['status'] != 'pending_or_running':
            rows.append(r)
    costs = [r.get('cost_usd') for r in rows]
    return {'runs': len(rows), 'correct': sum(r['status'] == 'correct' for r in rows),
            'mean_cost_usd': sum(costs)/len(costs) if costs and all(isinstance(c, (int, float)) for c in costs) else None}


def main():
    out = {'tasks': {}, 'note': 'n=3 per arm; primary comparison is Luna+advisor vs Luna alone (Anthropic framing)'}
    ledger = []
    for t in TASKS:
        solo = arm([run_row(f'luna-solo-v62-{t}-r{r}') for r in (1, 2, 3)])
        off = arm([run_row(f'official-v62-{t}-r{r}') for r in (1, 2, 3)])
        ref = advisor_solo(t)
        cm = (off['mean_cost_usd']/solo['mean_cost_usd']) if off['mean_cost_usd'] and solo['mean_cost_usd'] else None
        cr = (off['mean_cost_usd']/ref['mean_cost_usd']) if off['mean_cost_usd'] and ref['mean_cost_usd'] else None
        out['tasks'][t] = {'luna_solo': solo, 'luna_plus_advisor': off, 'advisor_solo_reference': ref,
                           'cost_multiple_vs_luna': cm, 'cost_ratio_vs_advisor_solo': cr}
        ledger += [r for r in solo['runs'] + off['runs'] if r['status'] != 'pending_or_running']
    probe = json.loads((ROOT/PROBE_COST).read_text())
    probe_cost = probe['tool_roundtrip']['cost_usd'] + sum(x['cost_usd'] for x in probe['sol_resume']) + probe['grok']['cost_usd']
    known = sum(r['cost_usd'] for r in ledger if isinstance(r['cost_usd'], (int, float)))
    out['ledger'] = {'runs': len(ledger), 'known_cost_usd': known, 'unknown_cost_runs': sum(r['cost_usd'] is None for r in ledger),
                     'protocol_probe_usd': probe_cost, 'total_known_usd': known + probe_cost}
    (ROOT/'stats-v62.json').write_text(json.dumps(out, indent=2) + '\n')
    for t, v in out['tasks'].items():
        s, o, r = v['luna_solo'], v['luna_plus_advisor'], v['advisor_solo_reference']
        print(f"{t:30s} Luna {s['correct']}/{s['finished']} ${s['mean_cost_usd']} | Luna+adv {o['correct']}/{o['finished']} ${o['mean_cost_usd']}"
              f" calls {o.get('mean_advisor_calls')} nudges {o.get('nudges')} | advisor solo {r['correct']}/{r['runs']} ${r['mean_cost_usd']}")
    print(out['ledger'])


if __name__ == '__main__':
    main()
