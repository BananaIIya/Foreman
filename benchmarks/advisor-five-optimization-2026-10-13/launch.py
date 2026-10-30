"""Serial immutable attempts. Failed model answers are never automatically retried."""
import argparse
import asyncio
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from daytona import AsyncDaytona
from daytona_api_client_async import OrganizationsApi

ROOT = Path(__file__).resolve().parent
HARBOR = '/private/tmp/kimi-k28-bin/harbor'


def record(data):
    data['at'] = datetime.now(timezone.utc).isoformat()
    with (ROOT / 'raw/launch-events.jsonl').open('a') as f:
        f.write(json.dumps(data) + '\n')
    print(json.dumps(data), flush=True)


async def wait_capacity(task):
    meta = json.loads((ROOT / 'raw/preflight/task-metadata.json').read_text())
    need = next(m['environment'] for m in meta if m['short_name'] == task)
    async with AsyncDaytona() as d:
        while True:
            boxes = [s async for s in d.list()]
            if not boxes:
                return
            usage = await OrganizationsApi(d._api_client).get_organization_usage_overview(boxes[0].organization_id)
            region = next(x for x in usage.to_dict()['regionUsage'] if x['regionId'] == 'us' and x['sandboxClass'] == 'container')
            available = {k: region['total' + n + 'Quota'] - region['current' + n + 'Usage']
                         for k, n in [('cpu', 'Cpu'), ('memory', 'Memory'), ('disk', 'Disk')]}
            if available['cpu'] >= need['cpus'] and available['memory'] >= need['memory_mb']/1024 and available['disk'] >= need['storage_mb']/1024:
                return
            record({'event': 'waiting_capacity', 'task': task, 'available': available})
            await asyncio.sleep(30)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', action='append', required=True)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    load_dotenv(os.environ["DAYTONA_ENV_FILE"])
    os.environ['CURSOR_API_KEY'] = json.loads(Path(os.environ["CURSOR_AUTH_JSON"]).read_text())['apiKey']
    os.environ['PYTHONPATH'] = str(ROOT)
    os.environ['NO_COLOR'] = '1'
    for name in args.config:
        config_path = ROOT / 'configs' / (name + '.json')
        config = json.loads(config_path.read_text())
        assert config['n_attempts'] == 1 and config['n_concurrent_trials'] == 1 and config['retry']['max_retries'] == 0
        cmd = [HARBOR, 'run', '--config', str(config_path), '--plugin', 'evidence:Evidence']
        if args.dry_run:
            subprocess.run(cmd + ['--dry-run'], check=True)
            continue
        guard = ROOT / 'raw' / (name + '-launch-once.json')
        with guard.open('x') as f:
            json.dump({'started_at': datetime.now(timezone.utc).isoformat(), 'pid': os.getpid(),
                       'config_sha256': hashlib.sha256(config_path.read_bytes()).hexdigest(),
                       'runtime_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.glob('*.py')}}, f, indent=2)
        task = Path(config['tasks'][0]['path']).name
        asyncio.run(wait_capacity(task))
        record({'event': 'harbor_start', 'name': name, 'task': task})
        with (ROOT / 'raw' / (name + '-console.log')).open('w') as output:
            run = subprocess.run(cmd, stdout=output, stderr=subprocess.STDOUT)
        results = list((Path(config['jobs_dir']) / config['job_name']).glob('*/result.json'))
        if len(results) != 1:
            raise RuntimeError('Expected one final trial result; inspect before resuming')
        result = json.loads(results[0].read_text())
        record({'event': 'harbor_end', 'name': name, 'returncode': run.returncode,
                'exception': result.get('exception_info'), 'reward': result.get('verifier_result'),
                'result': str(results[0])})
        if run.returncode or result.get('exception_info'):
            raise RuntimeError('Infrastructure failure; do not mask it as a model answer')


if __name__ == '__main__':
    main()
