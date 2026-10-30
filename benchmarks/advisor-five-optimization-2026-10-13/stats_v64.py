"""V64 (Haiku executor) one-round table, next to the V62/V63 Luna rows and the advisor-solo reference.

Status comes straight from each trial's verifier result: the older result_for() expects Codex completion
files that the Claude SDK executor does not write. Costs come from v62-usage.json. Runs no models.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TASKS = ['cad-model', 'hof-topology-interpenetration', 'satb-audio-transcription', 'wal-recovery-ordering', 'atrx-vep-crispr']


def run_row(name):
    paths = list((ROOT/'raw/jobs'/name).glob('*/result.json'))
    if not paths:
        return {'run': name, 'status': 'pending'}
    res = json.loads(paths[0].read_text())
    trial = paths[0].parent
    ctrf = trial/'verifier'/'ctrf.json'
    tests = json.loads(ctrf.read_text())['results']['summary'] if ctrf.exists() else None
    reward = ((res.get('verifier_result') or {}).get('rewards') or {}).get('reward')
    status = 'invalid' if res.get('exception_info') or reward is None else ('correct' if reward == 1 else 'wrong')
    row = {'run': name, 'status': status, 'passed': tests and tests.get('passed'), 'tests': tests and tests.get('tests')}
    usage = trial/'agent'/'v62-usage.json'
    if usage.exists():
        u = json.loads(usage.read_text())
        row.update(cost_usd=u['total_cost_usd'], executor_cost_usd=u['executor']['cost_usd'],
                   models=u['executor'].get('models_in_session'), native_server_tools=u['executor'].get('native_server_tool_calls'))
        if u.get('advisor'):
            row.update(advisor_cost_usd=u['advisor']['cost_usd'], advisor_calls=sum(1 for c in u['advisor']['calls'] if c.get('ok')),
                       nudged=u['advisor']['final_review_nudge_sent'])
    return row


def main():
    luna = json.loads((ROOT/'stats-v63.json').read_text())['tasks'] if (ROOT/'stats-v63.json').exists() else {}
    out = {'tasks': {}, 'note': 'Haiku: one run per arm (n=1). Luna: n=3 from stats-v63.json.'}
    total = 0.
    for t in TASKS:
        solo, off = run_row(f'haiku-solo-v64-{t}-r1'), run_row(f'haiku-official-v64-{t}-r1')
        if off['status'] == 'invalid' and (ROOT/'raw/jobs'/f'haiku-official-v64-{t}-r1b').exists():
            # Pre-model setup failure replaced once under r1b (policy amendment); the original stays in the ledger.
            off = dict(run_row(f'haiku-official-v64-{t}-r1b'), replaced_invalid_run=off['run'])
        out['tasks'][t] = {'haiku_solo': solo, 'haiku_plus_advisor': off}
        total += sum(r.get('cost_usd') or 0 for r in (solo, off))
        lt = luna.get(t, {})
        ls = lt.get('luna_solo', {}); lo = lt.get('luna_plus_advisor', {}); ref = lt.get('advisor_solo_reference', {})
        sc = lambda a: [x['tests']['passed'] for x in a.get('runs', []) if x.get('tests')]
        print(f"{t:30s} Haiku {solo['status']} {solo.get('passed')}/{solo.get('tests')} ${solo.get('cost_usd')} | "
              f"Haiku+adv {off['status']} {off.get('passed')}/{off.get('tests')} ${off.get('cost_usd')} calls {off.get('advisor_calls')} | "
              f"Luna {sc(ls)} Luna+adv {sc(lo)} | solo {ref.get('correct')}/{ref.get('runs')}")
    out['known_cost_usd'] = total
    (ROOT/'stats-v64.json').write_text(json.dumps(out, indent=2) + '\n')
    print({'known_cost_usd': total})


if __name__ == '__main__':
    main()
