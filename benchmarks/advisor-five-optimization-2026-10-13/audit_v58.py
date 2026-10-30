"""Read-only V58 integrity audit plus the human acceptance policy. Runs no models."""
import hashlib, json, math
from pathlib import Path
from v9_runtime.metrics import aggregate_phases
from trial_validity_v2 import result_for
from acceptance_report import evaluate

ROOT = Path(__file__).resolve().parent
CEILING = .73
INITIAL_WORKER_SHARE = .06


def audit():
    manifest = json.loads((ROOT/'manifest.json').read_text())
    frozen = json.loads((ROOT/'raw/candidates/v58/manifest.json').read_text())
    problems, pending, rows = [], [], []
    for relative, digest in frozen['source_sha256'].items():
        if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest() != digest:
            problems.append('Frozen runtime changed: '+relative)
    original = {k: v for k, v in manifest['sources_sha256'].items() if k.startswith('tasks/')}
    for relative, digest in original.items():
        if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest() != digest:
            problems.append('Original task changed: '+relative)
    for task in manifest['tasks']:
        name = 'hybrid-v58-'+task
        summary, solo = result_for(name), result_for('solo-'+task)
        row = {'task': task, 'status': summary['status'], 'problems': []}
        paths = list((ROOT/'raw/jobs'/name).glob('*/result.json'))
        if not paths:
            pending.append(task); rows.append(row); continue
        if len(paths) != 1:
            row['problems'].append('Not exactly one attempt'); rows.append(row); continue
        agent = paths[0].parent/'agent'
        guard = ROOT/'raw'/(name+'-v58-runtime.json')
        if not guard.exists() or json.loads(guard.read_text()) != frozen:
            row['problems'].append('Per-attempt frozen runtime guard mismatch')
        if not (agent/'hybrid-result.json').exists():
            row['problems'].append('Missing hybrid completion'); rows.append(row); continue
        h = json.loads((agent/'hybrid-result.json').read_text())
        totals = aggregate_phases(agent/'phases')
        if h['assignment'] != frozen['assignment'][task]: row['problems'].append('Model assignment changed')
        if h['advisor_calls'] > 2: row['problems'].append('Advisor call bound exceeded')
        if h['preparations'] > 1: row['problems'].append('Repeated worker repair')
        if not totals['complete'] or not h['usage_complete']: row['problems'].append('Missing native usage (cost unknown, cannot qualify)')
        if h['cost_usd'] is not None and totals['cost_usd'] is not None and not math.isclose(totals['cost_usd'], h['cost_usd'], abs_tol=1e-10):
            row['problems'].append('Ledger and raw phase cost disagree')
        if h['verifier_feedback'] is not False: row['problems'].append('Verifier feedback not excluded')
        if not math.isclose(h['target_ceiling_usd'], h['baseline_cost_usd']*CEILING): row['problems'].append('Ceiling changed')
        from harbor.models.task.task import Task
        if h['instruction_sha256'] != hashlib.sha256(Task(ROOT/'tasks'/task).instruction.encode()).hexdigest():
            row['problems'].append('Original instruction changed')
        phases = h['phases']
        if not phases or phases[0]['phase'] != 'worker-discovery':
            row['problems'].append('Worker attempt was not the first paid phase')
        if any(p['phase'] == 'decision-plan' for p in phases): row['problems'].append('Unexpected planner phase')
        req = json.loads((agent/'phases/worker-discovery/request.json').read_text())
        if '"original_task":' not in req.get('prompt', ''): row['problems'].append('Worker did not receive the full original task')
        if req.get('budget_usd') is not None and not math.isclose(req['budget_usd'], min(solo['cost_usd']*CEILING, solo['cost_usd']*INITIAL_WORKER_SHARE), abs_tol=1e-10):
            row['problems'].append('Initial worker budget drift')
        prior, index = 0., 0
        for record in phases:
            if record['role'] == 'advisor':
                cp = agent/('checkpoint-budget-'+record['phase']+'.json')
                if not cp.exists():
                    row['problems'].append('Missing advisor checkpoint journal')
                else:
                    c = json.loads(cp.read_text())
                    available = max(0., solo['cost_usd']*CEILING-prior-min(.005, solo['cost_usd']*.03))
                    allowance = available*.70 if index == 0 else available
                    if not math.isclose(c['phase_allowance_usd'], allowance, abs_tol=1e-9):
                        row['problems'].append('Advisor allowance mismatch')
                index += 1
            prior += record['cost_usd'] or 0.
        for r_path in (agent/'phases').glob('*/request.json'):
            r = json.loads(r_path.read_text()); role = r_path.parent.name.split('-')[0]
            expected = frozen['assignment'][task].get(role)
            if expected is None: continue
            if role == 'advisor' and expected.startswith('cursor/'): expected = 'grok-4.7'
            if r.get('model') is not None and r['model'] != expected:
                row['problems'].append('Native model mismatch: '+r_path.parent.name)
        savings = 1-h['cost_usd']/solo['cost_usd'] if h['cost_usd'] is not None and solo.get('cost_usd') else None
        row.update(cost_usd=h['cost_usd'], known_cost_lower_bound_usd=h.get('known_cost_lower_bound_usd'),
                   solo_cost_usd=solo.get('cost_usd'), solo_status=solo['status'], savings_fraction=savings,
                   tests=summary.get('tests'), roles=totals['role_cost_usd'], terminal_reason=h['terminal_reason'])
        if summary['status'] == 'invalid': row['problems'].append('Trial did not produce a valid original score')
        rows.append(row)
    problems += [r['task']+': '+p for r in rows for p in r['problems']]
    acceptance = evaluate('v58')
    out = {'candidate': 'v58', 'pending': pending, 'problems': problems, 'original_files_checked': len(original),
           'frozen_files_checked': len(frozen['source_sha256']),
           'correct_tasks': acceptance['correct_tasks'], 'qualifying_tasks': acceptance['qualifying_tasks'],
           'successful_task_weighted_savings_fraction': acceptance['successful_task_weighted_savings_fraction'],
           'numerical_acceptance_pass': acceptance['numerical_acceptance_pass'],
           'goal_evidence_passes': acceptance['numerical_acceptance_pass'] and not problems,
           'rows': rows,
           'limitations': ['One frozen attempt per task; not a repeat-run stability estimate.',
                           'Standard native token price equivalents, not invoices; infrastructure excluded.',
                           'All development and failed attempts retained in expenses.json.']}
    (ROOT/'audit-v58.json').write_text(json.dumps(out, indent=2)+'\n')
    (ROOT/'acceptance-v58.json').write_text(json.dumps(acceptance, indent=2)+'\n')
    return out


if __name__ == '__main__':
    out = audit()
    print(json.dumps({k: v for k, v in out.items() if k not in ('rows', 'limitations')}, indent=1))
    for r in out['rows']:
        print(r['task'], r['status'], r.get('tests'), r.get('cost_usd'), r.get('savings_fraction'), r.get('terminal_reason'))
