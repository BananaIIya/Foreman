"""Read-only V60 integrity audit (3 runs per task) plus the statistical acceptance policy. Runs no models."""
import hashlib, json, math, re
from pathlib import Path
from harbor.models.task.task import Task
from v9_runtime.metrics import aggregate_phases
from trial_validity_v2 import result_for
from stats_acceptance_v60 import evaluate

ROOT = Path(__file__).resolve().parent
CANDIDATE = 'v60'
TASKS = ['cad-model', 'hof-topology-interpenetration', 'satb-audio-transcription', 'wal-recovery-ordering']
CEILING, INITIAL_WORKER_SHARE = .73, .06
# Heuristic: shell or Python writes by the worker whose target is under /app but not /app/worker/.
WRITE = re.compile(r"(?:>>?|tee(?:\s+-a)?|cat\s*>)\s*['\"]?(/app/[^\s'\"<>|;)]+)")  # reads via open() are not writes


def worker_writes_outside(agent):
    hits = []
    for events in (agent/'phases').glob('worker-*/app-server.events.jsonl'):
        for line in events.read_text().splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get('method') != 'item/completed':
                continue
            item = e['params']['item']
            if item.get('type') == 'fileChange':
                hits += [c.get('path') for c in item.get('changes', []) if not str(c.get('path', '')).startswith('/app/worker/')]
            if item.get('type') == 'commandExecution':
                hits += [p for p in WRITE.findall(str(item.get('command') or '')) if not p.startswith('/app/worker')]
    return sorted(set(h for h in hits if h))


def audit_run(name, task, frozen, solo):
    row = {'run': name, 'task': task, 'status': result_for(name)['status'], 'problems': [], 'notes': []}
    paths = list((ROOT/'raw/jobs'/name).glob('*/result.json'))
    if not paths:
        row['status'] = 'pending'; return row
    if len(paths) != 1:
        row['problems'].append('Not exactly one attempt'); return row
    agent = paths[0].parent/'agent'
    guard = ROOT/'raw'/(name+'-'+CANDIDATE+'-runtime.json')
    if not guard.exists() or json.loads(guard.read_text()) != frozen:
        row['problems'].append('Per-attempt frozen runtime guard mismatch')
    if not (agent/'hybrid-result.json').exists():
        row['problems'].append('Missing hybrid completion (infrastructure failure?)'); return row
    h = json.loads((agent/'hybrid-result.json').read_text())
    totals = aggregate_phases(agent/'phases')
    if h['assignment'] != frozen['assignment'][task]: row['problems'].append('Model assignment changed')
    if h['advisor_calls'] > 2: row['problems'].append('Advisor call bound exceeded')
    if h['preparations'] > 1: row['problems'].append('Repeated worker repair')
    if not totals['complete'] or not h['usage_complete']: row['problems'].append('Missing native usage (cost unknown)')
    if h['cost_usd'] is not None and totals['cost_usd'] is not None and not math.isclose(totals['cost_usd'], h['cost_usd'], abs_tol=1e-10):
        row['problems'].append('Ledger and raw phase cost disagree')
    if h['verifier_feedback'] is not False: row['problems'].append('Verifier feedback not excluded')
    if not math.isclose(h['target_ceiling_usd'], h['baseline_cost_usd']*CEILING): row['problems'].append('Ceiling changed')
    if h['instruction_sha256'] != hashlib.sha256(Task(ROOT/'tasks'/task).instruction.encode()).hexdigest():
        row['problems'].append('Original instruction changed')
    phases = h['phases']
    if not phases or phases[0]['phase'] != 'worker-discovery': row['problems'].append('Worker groundwork was not the first paid phase')
    req = json.loads((agent/'phases/worker-discovery/request.json').read_text())
    if '"original_task":' not in req.get('prompt', ''): row['problems'].append('Worker did not receive the full original task')
    if req.get('budget_usd') is not None and not math.isclose(req['budget_usd'], solo*INITIAL_WORKER_SHARE, abs_tol=1e-10):
        row['problems'].append('Initial worker budget drift')
    prior = 0.
    for record in phases:
        if record['role'] == 'advisor':
            cp = agent/('checkpoint-budget-'+record['phase']+'.json')
            if cp.exists():
                c = json.loads(cp.read_text())
                allowance = max(0., solo*CEILING-prior-min(.005, solo*.03))  # V60: no 70/30 split
                if not math.isclose(c['phase_allowance_usd'], allowance, abs_tol=1e-9):
                    row['problems'].append('Advisor allowance mismatch')
            else:
                row['problems'].append('Missing advisor checkpoint journal')
        prior += record['cost_usd'] or 0.
    for r_path in (agent/'phases').glob('*/request.json'):
        r = json.loads(r_path.read_text()); role = r_path.parent.name.split('-')[0]
        expected = frozen['assignment'][task].get(role)
        if expected is None: continue
        if role == 'advisor' and expected.startswith('cursor/'): expected = 'grok-4.7'
        if r.get('model') is not None and r['model'] != expected:
            row['problems'].append('Native model mismatch: '+r_path.parent.name)
    for sdk in (agent/'phases').glob('advisor-*/sdk-result.json'):
        resumes = json.loads(sdk.read_text()).get('transport_resumes', 0)
        if resumes: row['notes'].append(f'{sdk.parent.name}: transport resume used ({resumes})')
    outside = worker_writes_outside(agent)
    if outside: row['notes'].append('Worker wrote outside /app/worker/ (prompt-only rule): '+', '.join(outside[:8]))
    row.update(cost_usd=h['cost_usd'], known_cost_lower_bound_usd=h.get('known_cost_lower_bound_usd'),
               tests=result_for(name).get('tests'), roles=totals['role_cost_usd'], terminal_reason=h['terminal_reason'])
    return row


def audit():
    manifest = json.loads((ROOT/'manifest.json').read_text())
    frozen = json.loads((ROOT/'raw/candidates'/CANDIDATE/'manifest.json').read_text())
    problems = []
    for relative, digest in frozen['source_sha256'].items():
        if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest() != digest:
            problems.append('Frozen runtime changed: '+relative)
    original = {k: v for k, v in manifest['sources_sha256'].items() if k.startswith('tasks/')}
    for relative, digest in original.items():
        if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest() != digest:
            problems.append('Original task changed: '+relative)
    rows = []
    for task in TASKS:
        solo = result_for('solo-'+task)['cost_usd']  # runtime ceiling uses the original solo (frozen)
        for run in (1, 2, 3):
            rows.append(audit_run(f'hybrid-{CANDIDATE}-{task}-r{run}', task, frozen, solo))
    problems += [r['run']+': '+p for r in rows for p in r['problems']]
    stats = evaluate(CANDIDATE)
    out = {'candidate': CANDIDATE, 'problems': problems, 'original_files_checked': len(original),
           'frozen_files_checked': len(frozen['source_sha256']), 'runs': rows,
           'statistical_acceptance': {k: stats[k] for k in ('all_runs_complete', 'tasks_passing', 'aggregate_savings_fraction', 'accept')},
           'goal_evidence_passes': stats['accept'] and not problems}
    (ROOT/f'audit-{CANDIDATE}.json').write_text(json.dumps(out, indent=2)+'\n')
    return out


if __name__ == '__main__':
    out = audit()
    for r in out['runs']:
        print(r['run'], r['status'], r.get('cost_usd'), r.get('terminal_reason'), r['problems'], r['notes'])
    print({k: out[k] for k in ('problems', 'statistical_acceptance', 'goal_evidence_passes')})
