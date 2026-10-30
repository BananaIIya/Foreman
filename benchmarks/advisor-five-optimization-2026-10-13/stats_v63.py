"""Final official-advisor-pattern table: Luna alone vs Luna + advisor (primary), advisor solo (secondary).

CAD/HOF/SATB official arm from V62 (Sol advisor). WAL/ATRX official arm from V63 (Sol advisor; the V62
Grok-advised runs are listed separately as transport-affected). Luna-alone arm from V62 for all tasks.
Reads grader results and per-run v62-usage.json; runs no models. n = 3 per arm.
"""
import json
from pathlib import Path
from stats_v62 import run_row, arm, advisor_solo, TASKS, PROBE_COST

ROOT = Path(__file__).resolve().parent
OFFICIAL = {'cad-model': 'v62', 'hof-topology-interpenetration': 'v62', 'satb-audio-transcription': 'v62',
            'wal-recovery-ordering': 'v63', 'atrx-vep-crispr': 'v63'}


def main():
    out = {'tasks': {}, 'note': 'n=3 per arm. Primary: Luna+advisor vs Luna alone (Anthropic framing).'}
    ledger = []
    for t in TASKS:
        solo = arm([run_row(f'luna-solo-v62-{t}-r{r}') for r in (1, 2, 3)])
        off = arm([run_row(f'official-{OFFICIAL[t]}-{t}-r{r}') for r in (1, 2, 3)])
        ref = advisor_solo(t)
        row = {'luna_solo': solo, 'luna_plus_advisor': off, 'official_version': OFFICIAL[t], 'advisor_solo_reference': ref,
               'cost_multiple_vs_luna': off['mean_cost_usd']/solo['mean_cost_usd'] if off['mean_cost_usd'] and solo['mean_cost_usd'] else None,
               'cost_ratio_vs_advisor_solo': off['mean_cost_usd']/ref['mean_cost_usd'] if off['mean_cost_usd'] and ref['mean_cost_usd'] else None,
               'mean_score_lift': (off['mean_tests_passed_fraction'] - solo['mean_tests_passed_fraction'])
                                  if off['mean_tests_passed_fraction'] is not None and solo['mean_tests_passed_fraction'] is not None else None}
        if OFFICIAL[t] == 'v63':
            row['v62_grok_advised_runs'] = [run_row(f'official-v62-{t}-r1')]
        out['tasks'][t] = row
        ledger += [r for r in solo['runs'] + off['runs'] + row.get('v62_grok_advised_runs', []) if r['status'] != 'pending_or_running']
    probe = json.loads((ROOT/PROBE_COST).read_text())
    probe_cost = probe['tool_roundtrip']['cost_usd'] + sum(x['cost_usd'] for x in probe['sol_resume']) + probe['grok']['cost_usd']
    known = sum(r['cost_usd'] for r in ledger if isinstance(r['cost_usd'], (int, float)))
    out['ledger'] = {'runs': len(ledger), 'known_cost_usd': known, 'protocol_probe_usd': probe_cost,
                     'total_known_usd': known + probe_cost,
                     'incomplete_cost_runs': ['official-v62-atrx-vep-crispr-r1 (advisor call 2 abandoned at close; lower bound)']}
    (ROOT/'stats-v63.json').write_text(json.dumps(out, indent=2) + '\n')
    for t, v in out['tasks'].items():
        s, o, r = v['luna_solo'], v['luna_plus_advisor'], v['advisor_solo_reference']
        sc = lambda a: [x['tests']['passed'] for x in a['runs'] if x.get('tests')]
        print(f"{t:30s} Luna {s['correct']}/{s['finished']} {sc(s)} ${s['mean_cost_usd']:.4f} | +adv({v['official_version']}) {o['correct']}/{o['finished']} {sc(o)}"
              f" ${(o['mean_cost_usd'] or 0):.4f} x{(v['cost_multiple_vs_luna'] or 0):.1f} share {o.get('advisor_cost_share')} | solo {r['correct']}/{r['runs']} ${r['mean_cost_usd']:.4f}")
    print(out['ledger'])


if __name__ == '__main__':
    main()
