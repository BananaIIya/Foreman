"""Resume the two still-unused authorizations after the failed CAD run stopped."""
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = '/private/tmp/kimi-k28-uv-tools/harbor/bin/python'

def main():
    stopped = json.loads((ROOT / 'raw/v10-cad-transport-failure.json').read_text())
    assert stopped['confirmed_all_prior_processes_exited']
    assert stopped['cohort_exit_code'] == stopped['queued_pair_exit_code'] == 130
    supplement = json.loads((ROOT / 'raw/candidates/v10/authorized-pair.json').read_text())
    for relative, expected in supplement['source_sha256'].items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected, relative
    authorization_path = ROOT / 'authorization-wal-atrx-20261013.json'
    authorization = json.loads(authorization_path.read_text())
    assert authorization['answer'] == '允许，两题各重跑一次'
    assert not authorization['launched_fresh_attempts']
    for task in ['wal-recovery-ordering', 'atrx-vep-crispr']:
        name = 'hybrid-v10-' + task
        assert authorization['authorized_fresh_attempts'][task] == 1
        assert not (ROOT / 'raw' / (name + '-launch-once.json')).exists()
        authorization['launched_fresh_attempts'][task] = {
            'job_name': name,
            'launch_requested_at': datetime.now(timezone.utc).isoformat(),
            'automatic_retries': 0,
        }
        authorization_path.write_text(json.dumps(authorization, indent=2, ensure_ascii=False) + '\n')
        subprocess.run([PYTHON, '-m', 'v10_runtime.launch', '--config', name], cwd=ROOT, check=True)

if __name__ == '__main__':
    main()
