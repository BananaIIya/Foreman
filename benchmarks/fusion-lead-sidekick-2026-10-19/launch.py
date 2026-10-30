"""Launch-once Harbor trials for the Fusion experiment. No automatic retries, no score-based reruns.

  python launch.py plan                      # write configs/ and print the queue (no model calls)
  python launch.py run NAME [NAME ...]       # run these trials, CONCURRENCY at a time
  python launch.py run --all                 # every planned trial not yet launched

Each trial name may be launched once (raw/<name>-launch-once.json is created exclusively). Before each launch the
known spend of every launched trial is summed from its usage.json; nothing new starts once it reaches BUDGET_USD.
A trial still running when the ceiling is hit is not stopped, so the final total can exceed the ceiling by up to
CONCURRENCY trials.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
HARBOR = str(ROOT / '.venv/bin/harbor')
DAYTONA_ENV = Path(os.environ.get('FUSION_DAYTONA_ENV', Path.home() / '.config/fusion-bench/daytona.env'))
BUDGET_USD = float(os.environ.get('FUSION_BUDGET_USD', '30'))
CONCURRENCY = int(os.environ.get('FUSION_CONCURRENCY', '1'))
ARMS = {'opus-solo': ('fusion_arms:OpusSolo', 'anthropic/claude-opus-5-5'),
        'fusion': ('fusion_arms:Fusion', 'anthropic/claude-opus-5-5+claude-haiku-5-5'),
        'haiku-solo': ('fusion_arms:HaikuSolo', 'anthropic/claude-haiku-5-5'),
        'haiku-advisor': ('fusion_arms:HaikuAdvisor', 'anthropic/claude-haiku-5-5+advisor-claude-opus-5-5'),
        'sol-fusion': ('sol_fusion:SolFusion', 'openai/gpt-6.1-sol+claude-haiku-5-5')}
# Harbor and the Claude CLIs it starts get only these variables, not the launching desktop session's environment.
KEEP_ENV = ('PATH', 'HOME', 'USER', 'LOGNAME', 'TMPDIR', 'LANG', 'SHELL')
_lock = threading.RLock()


def child_env():
    env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
    secrets = {k: v for k, v in dotenv_values(DAYTONA_ENV).items() if v}
    if 'DAYTONA_API_KEY' not in secrets:
        sys.exit(f'No DAYTONA_API_KEY in {DAYTONA_ENV}')
    env.update(secrets)
    env.update(PYTHONPATH=str(ROOT), NO_COLOR='1')
    return env


def planned():
    """Task-major order with the arm order rotated per task, so arms share time windows and none always goes first."""
    tasks = [t['task'] for t in json.loads((ROOT / 'selection.json').read_text())['tasks']]
    arms = list(ARMS)
    names = []
    for i, task in enumerate(tasks):
        for arm in arms[i % len(arms):] + arms[:i % len(arms)]:
            names.append(f'{arm}-{task}-r1')
    return names


def config(name):
    arm = max((a for a in ARMS if name.startswith(a + '-')), key=len)
    task = name[len(arm) + 1:].rsplit('-r', 1)[0]
    import_path, model = ARMS[arm]
    return {'job_name': name, 'jobs_dir': str(ROOT / 'raw/jobs'), 'n_attempts': 1, 'n_concurrent_trials': 1,
            'retry': {'max_retries': 0}, 'environment': {'type': 'daytona', 'delete': True},
            'tasks': [{'path': str(ROOT / 'raw/tasks' / task)}],
            'agents': [{'import_path': import_path, 'model_name': model, 'kwargs': {}}]}


def spent():
    total, unknown = 0.0, []
    for guard in (ROOT / 'raw').glob('*-launch-once.json'):
        name = guard.name[:-len('-launch-once.json')]
        usage = list((ROOT / 'raw/jobs' / name).glob('*/agent/usage.json'))
        cost = json.loads(usage[0].read_text()).get('total_cost_usd') if usage else None
        if isinstance(cost, (int, float)):
            total += cost
        else:
            unknown.append(name)
    return total, unknown


def record(d):
    d['at'] = datetime.now(timezone.utc).isoformat()
    with _lock, (ROOT / 'raw/launch-events.jsonl').open('a') as f:
        f.write(json.dumps(d) + '\n')
    print(json.dumps(d), flush=True)


def run_one(name, env):
    with _lock:
        total, unknown = spent()
        if total >= BUDGET_USD:
            record({'event': 'skipped_budget', 'name': name, 'known_spend': round(total, 4), 'unknown': unknown})
            return
        with (ROOT / 'raw' / f'{name}-launch-once.json').open('x') as f:
            json.dump({'started_at': datetime.now(timezone.utc).isoformat(),
                       'source_sha256': {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest()
                                         for f in ('fusion_arms.py', 'sol_fusion.py')},
                       'child_env_names': sorted(env), 'known_spend_before': round(total, 4)}, f)
    record({'event': 'harbor_start', 'name': name})
    with (ROOT / 'raw' / f'{name}-console.log').open('w') as out:
        r = subprocess.run([HARBOR, 'run', '--config', str(ROOT / 'configs' / f'{name}.json')],
                           stdout=out, stderr=subprocess.STDOUT, env=env, cwd=ROOT)
    res = list((ROOT / 'raw/jobs' / name).glob('*/result.json'))
    data = json.loads(res[0].read_text()) if res else {}
    usage = list((ROOT / 'raw/jobs' / name).glob('*/agent/usage.json'))
    cost = json.loads(usage[0].read_text()).get('total_cost_usd') if usage else None
    record({'event': 'harbor_end', 'name': name, 'returncode': r.returncode,
            'exception': (data.get('exception_info') or {}).get('exception_type'),
            'reward': ((data.get('verifier_result') or {}).get('rewards') or {}).get('reward'), 'cost_usd': cost})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['plan', 'run'])
    ap.add_argument('names', nargs='*')
    ap.add_argument('--all', action='store_true')
    a = ap.parse_args()
    (ROOT / 'configs').mkdir(exist_ok=True)
    names = planned()
    for n in names:
        (ROOT / 'configs' / f'{n}.json').write_text(json.dumps(config(n), indent=2) + '\n')
    if a.cmd == 'plan':
        print('\n'.join(names))
        print(f'{len(names)} trials; budget ceiling ${BUDGET_USD}; concurrency {CONCURRENCY}; known spend so far {spent()}')
        return
    queue = [n for n in (names if a.all else a.names) if not (ROOT / 'raw' / f'{n}-launch-once.json').exists()]
    unknown = [n for n in queue if n not in names]
    if unknown:
        sys.exit(f'Not planned: {unknown}')
    env = child_env()
    pending = list(queue)

    def worker():
        while True:
            with _lock:
                if not pending:
                    return
                name = pending.pop(0)
            run_one(name, env)
    threads = [threading.Thread(target=worker) for _ in range(min(CONCURRENCY, len(queue)))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(json.dumps({'event': 'queue_done', 'known_spend': spent()}))


if __name__ == '__main__':
    main()
