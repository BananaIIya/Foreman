"""One explicitly authorized fresh WAL and ATRX attempt after the live cohort."""
import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = '/private/tmp/kimi-k28-uv-tools/harbor/bin/python'
TASKS = ['wal-recovery-ordering', 'atrx-vep-crispr']


def wait_finalized(name):
    while True:
        events = [json.loads(line) for line in (ROOT / 'raw/launch-events.jsonl').read_text().splitlines()]
        ends = [event for event in events if event.get('event') == 'harbor_end' and event.get('name') == name]
        if ends:
            assert len(ends) == 1 and not ends[0].get('returncode') and not ends[0].get('exception')
            return
        time.sleep(15)


def main():
    supplement = json.loads((ROOT / 'raw/candidates/v10/authorized-pair.json').read_text())
    for relative, expected in supplement['source_sha256'].items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected, relative
    authorization_path = ROOT / 'authorization-wal-atrx-20261013.json'
    authorization = json.loads(authorization_path.read_text())
    assert authorization['answer'] == '允许，两题各重跑一次'
    assert all(authorization['authorized_fresh_attempts'][task] == 1 for task in TASKS)
    assert not authorization['launched_fresh_attempts']
    print('Waiting for the HOF/SATB/CAD cohort to finish; no new model calls yet.', flush=True)
    previous = 'hybrid-v10-cad-model'
    for task in TASKS:
        wait_finalized(previous)
        name = 'hybrid-v10-' + task
        assert not (ROOT / 'raw' / (name + '-launch-once.json')).exists()
        authorization = json.loads(authorization_path.read_text())
        assert task not in authorization['launched_fresh_attempts']
        authorization['launched_fresh_attempts'][task] = {
            'job_name': name,
            'launch_requested_at': datetime.now(timezone.utc).isoformat(),
            'automatic_retries': 0,
        }
        authorization_path.write_text(json.dumps(authorization, indent=2, ensure_ascii=False) + '\n')
        subprocess.run([PYTHON, '-m', 'v10_runtime.launch', '--config', name], cwd=ROOT, check=True)
        previous = name


if __name__ == '__main__':
    main()
