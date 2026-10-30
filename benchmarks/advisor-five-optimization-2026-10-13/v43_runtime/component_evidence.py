"""Summarize task-local invocation receipts; no claim of semantic correctness."""
import hashlib
import json
from pathlib import Path


def summarize(agent_dir, artifact_dir):
    agent_dir, artifact_dir = Path(agent_dir), Path(artifact_dir)
    hashes = {hashlib.sha256(p.read_bytes()).hexdigest()
              for p in agent_dir.glob('worker-component-worker-*-module.py')}
    source = agent_dir/'worker-component-final-receipts.jsonl'
    if not source.exists():
        return {'available': False, 'correctness_proven': False, 'original_task_runs': 0}
    rows = [json.loads(line) for line in source.read_text().splitlines() if line.strip()]
    runs = []
    for row in rows:
        if row.get('status') != 'completed' or row.get('purpose') != 'original_task':
            continue
        matches = []
        for file in row.get('files', []):
            name = file.get('path', '')
            # Only known task output paths; never follow a receipt outside artifacts.
            relative = Path(name.lstrip('/'))
            if not name.startswith('/app/') or '..' in relative.parts:
                continue
            local = artifact_dir/relative
            if local.is_file() and hashlib.sha256(local.read_bytes()).hexdigest() == file.get('sha256'):
                matches.append(name)
        runs.append({'invocation_id': row.get('invocation_id'),
                     'module_matches_captured_worker_source': row.get('module_sha256') in hashes,
                     'matching_final_artifacts': matches})
    return {'available': True, 'original_task_runs': len(runs), 'runs': runs,
            'correctness_proven': False, 'model_calls': 0,
            'limitations': 'Task-local execution receipts and byte correspondence show recorded reuse, not semantic correctness or counterfactual cost saved.'}
