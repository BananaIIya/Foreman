"""Apply the human's current acceptance policy without rewriting frozen reports."""
import argparse
import json
import math
import re
from pathlib import Path
from trial_validity_v2 import result_for

ROOT = Path(__file__).resolve().parent


def evaluate(candidate):
    if not re.fullmatch(r'v[0-9]+', candidate):
        raise ValueError('Expected one frozen candidate identifier')
    policy = json.loads((ROOT/'acceptance-policy-20261014.json').read_text())
    manifest = json.loads((ROOT/'raw/candidates'/candidate/'manifest.json').read_text())
    tasks = json.loads((ROOT/'manifest.json').read_text())['tasks']
    rows = []
    for task in tasks:
        solo, hybrid = result_for('solo-'+task), result_for('hybrid-'+candidate+'-'+task)
        comparable = (solo['status'] == 'correct'
                      and isinstance(solo.get('cost_usd'), (int, float))
                      and solo['cost_usd'] > 0
                      and isinstance(hybrid.get('cost_usd'), (int, float))
                      and math.isfinite(hybrid['cost_usd']) and hybrid['cost_usd'] >= 0)
        savings = 1-hybrid['cost_usd']/solo['cost_usd'] if comparable else None
        rows.append({'task': task, 'solo': solo, 'hybrid': hybrid,
                     'savings_fraction': savings,
                     'qualifies': hybrid['status'] == 'correct' and savings is not None
                         and savings + 1e-12 >= policy['minimum_savings_per_successful_task']})
    successful = [r for r in rows if r['hybrid']['status'] == 'correct']
    cost_complete = bool(successful) and all(r['savings_fraction'] is not None for r in successful)
    baseline = sum(r['solo']['cost_usd'] for r in successful) if cost_complete else None
    cost = sum(r['hybrid']['cost_usd'] for r in successful) if cost_complete else None
    savings = 1-cost/baseline if cost_complete else None
    numerical_pass = (len(successful) >= policy['minimum_correct_tasks']
                      and all(r['qualifies'] for r in successful)
                      and savings is not None
                      and savings + 1e-12 >= policy['minimum_weighted_aggregate_savings_successful_tasks'])
    return {'candidate': candidate, 'frozen_at': manifest['frozen_at'],
            'policy': policy, 'tasks': rows, 'correct_tasks': len(successful),
            'qualifying_tasks': sum(r['qualifies'] for r in rows),
            'successful_task_baseline_cost_usd': baseline,
            'successful_task_hybrid_cost_usd': cost,
            'successful_task_weighted_savings_fraction': savings,
            'numerical_acceptance_pass': numerical_pass,
            'final_integrity_audit_required': True,
            'all_actual_attempt_costs': 'expenses.json',
            'historical_versions_combined': False,
            'cost_basis': 'Native token standard-rate equivalent, not invoice; infrastructure excluded'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('candidate')
    args = parser.parse_args()
    report = evaluate(args.candidate)
    (ROOT/('acceptance-'+args.candidate+'.json')).write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k: report[k] for k in ('candidate', 'correct_tasks', 'qualifying_tasks',
        'successful_task_weighted_savings_fraction', 'numerical_acceptance_pass')}))
