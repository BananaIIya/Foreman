"""Offline summary of every finished trial (no model calls). Writes stats.json and prints a table."""
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARMS = ('opus-solo', 'fusion', 'sol-fusion', 'haiku-advisor', 'haiku-solo')


def trial(name):
    res = list((ROOT / 'raw/jobs' / name).glob('*/result.json'))
    if not res:
        return None
    d = json.loads(res[0].read_text())
    tdir = res[0].parent
    usage = json.loads((tdir / 'agent/usage.json').read_text()) if (tdir / 'agent/usage.json').exists() else {}
    ctrf = tdir / 'verifier/ctrf.json'
    checks = None
    if ctrf.exists():
        s = json.loads(ctrf.read_text()).get('results', {}).get('summary', {})
        checks = [s.get('passed'), s.get('tests')]
    reward = ((d.get('verifier_result') or {}).get('rewards') or {}).get('reward')
    exc = (d.get('exception_info') or {}).get('exception_type')
    sessions = usage.get('sessions') or {}
    side = sessions.get('sidekick') or {}
    lead = sessions.get('lead') or sessions.get('solo') or {}
    ev = tdir / 'agent/events.jsonl'
    advisor_calls = sum(1 for l in ev.read_text().splitlines() if '"server_tool_use"' in l) if ev.exists() else 0
    return {'advisor_calls': advisor_calls,
            'per_model_cost_usd': {k: v.get('per_model_cost_usd') for k, v in sessions.items() if v},
            'reward': reward, 'exception': exc, 'checks': checks, 'cost_usd': usage.get('total_cost_usd'),
            'lead_or_solo_cost_usd': lead.get('cost_usd'), 'sidekick_cost_usd': side.get('cost_usd'),
            'delegations': len(side.get('delegations') or []), 'lead_turns': lead.get('num_turns'),
            'sidekick_turns': side.get('num_turns'), 'wall_seconds': usage.get('wall_seconds'),
            'outcome': usage.get('outcome'), 'models': {k: v.get('models_in_session') for k, v in sessions.items() if v}}


def main():
    rows = {}
    for guard in sorted((ROOT / 'raw').glob('*-launch-once.json')):
        name = guard.name[:-len('-launch-once.json')]
        rows[name] = trial(name)
    by_arm = defaultdict(list)
    for name, r in rows.items():
        arm = max((a for a in ARMS if name.startswith(a + '-')), key=len)
        task = name[len(arm) + 1:].rsplit('-r', 1)[0]
        by_arm[arm].append((task, r))
    summary = {}
    for arm in ARMS:
        done = [(t, r) for t, r in by_arm.get(arm, []) if r and r['reward'] is not None]
        costs = [r['cost_usd'] for _, r in done if isinstance(r['cost_usd'], (int, float))]
        summary[arm] = {'finished_with_reward': len(done), 'passed': sum(1 for _, r in done if r['reward'] == 1),
                        'total_cost_usd': round(sum(costs), 6), 'cost_complete': len(costs) == len(done),
                        'infra_or_unfinished': [t for t, r in by_arm.get(arm, []) if not r or r['reward'] is None]}
    (ROOT / 'stats.json').write_text(json.dumps({'summary': summary, 'trials': rows}, indent=2) + '\n')
    tasks = sorted({n[len(a) + 1:].rsplit('-r', 1)[0] for n in rows for a in ARMS if n.startswith(a + '-')})
    print('task'.ljust(32) + ''.join(a.ljust(30) for a in ARMS))
    for t in tasks:
        cells = []
        for a in ARMS:
            r = rows.get(f'{a}-{t}-r1')
            if not r:
                cells.append('-'.ljust(30))
                continue
            chk = f"{r['checks'][0]}/{r['checks'][1]}" if r['checks'] else '?'
            cost = f"${r['cost_usd']:.3f}" if isinstance(r['cost_usd'], (int, float)) else '$?'
            extra = f" d{r['delegations']}" if a in ('fusion', 'sol-fusion') else (f" a{r['advisor_calls']}" if a == 'haiku-advisor' else '')
            cells.append(f"{r['reward']} {chk} {cost}{extra}".ljust(30))
        print(t.ljust(32) + ''.join(cells))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
