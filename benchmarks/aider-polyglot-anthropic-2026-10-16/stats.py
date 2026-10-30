"""Per-arm pass rate and cost for the Anthropic Aider Polyglot experiment. Reads verifier results and usage.json."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARMS = ['opus-solo', 'haiku-solo', 'plan-write', 'native-advisor', 'opus-lean', 'leanplan-write']


def run(name):
    res = list((ROOT/'raw/jobs'/name).glob('*/result.json'))
    if not res:
        return None
    data = json.loads(res[0].read_text())
    reward = ((data.get('verifier_result') or {}).get('rewards') or {}).get('reward')
    exc = (data.get('exception_info') or {}).get('exception_type')
    stdout = res[0].parent/'verifier'/'test-stdout.txt'
    verifier_ran = stdout.exists()
    log = stdout.read_text(errors='replace') if verifier_ran else ''
    if 'Downloading https://services.gradle.org' in log and 'Connection reset' in log:
        # The verifier could not download Gradle from the sandbox; the answer was never tested.
        status = 'invalid'
    elif exc == 'RewardFileNotFoundError' and verifier_ran:
        # The verifier ran but aborted before writing a reward (e.g. the submitted code does not compile): a failed answer.
        status = 'fail'
    else:
        status = 'invalid' if exc or reward is None else ('pass' if reward == 1 else 'fail')
    row = {'status': status, 'cost': None, 'by_model': {}, 'advisor_calls': 0}
    u = res[0].parent/'agent'/'usage.json'
    if u.exists():
        usage = json.loads(u.read_text())
        row['cost'] = usage['total_cost_usd']
        for s in usage['stages']:
            for m, c in (s.get('per_model_cost_usd') or {}).items():
                row['by_model'][m] = row['by_model'].get(m, 0) + (c or 0)
            row['advisor_calls'] += sum(1 for t in s.get('server_tool_calls', []) if t == 'advisor')
    return row


def main():
    tasks = json.loads((ROOT/'selection.json').read_text())['tasks']
    out = {}
    for arm in ARMS:
        rows = {t: run(f'{arm}-{t}') for t in tasks}
        done = {t: r for t, r in rows.items() if r and r['status'] != 'invalid'}
        costs = [r['cost'] for r in done.values() if isinstance(r['cost'], (int, float))]
        by = {}
        for r in done.values():
            for m, c in r['by_model'].items():
                by[m] = by.get(m, 0) + c
        out[arm] = {'finished': len(done), 'passed': sum(r['status'] == 'pass' for r in done.values()),
                    'invalid': sum(1 for r in rows.values() if r and r['status'] == 'invalid'),
                    'total_cost': sum(costs), 'mean_cost': sum(costs)/len(costs) if costs else None,
                    'cost_by_model': by, 'advisor_calls': sum(r['advisor_calls'] for r in done.values()),
                    'per_task': {t: (r['status'], r['cost']) if r else None for t, r in rows.items()}}
    (ROOT/'stats.json').write_text(json.dumps(out, indent=2) + '\n')
    for arm, v in out.items():
        print(f"{arm:15s} pass {v['passed']}/{v['finished']} (invalid {v['invalid']}) total ${v['total_cost']:.3f} "
              f"mean ${v['mean_cost'] or 0:.4f} by-model { {k: round(c, 3) for k, c in v['cost_by_model'].items()} } advisor calls {v['advisor_calls']}")


if __name__ == '__main__':
    main()
