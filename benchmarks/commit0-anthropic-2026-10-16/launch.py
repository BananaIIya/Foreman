"""Serial, launch-once Harbor runs for the Anthropic Commit0 experiment. No automatic retries."""
import json, os, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
from dotenv import dotenv_values
ROOT = Path(__file__).resolve().parent
HARBOR = '/private/tmp/kimi-k28-bin/harbor'
# Harbor and the Claude CLIs it starts get only these variables, not the launching desktop session's environment
# (which carries its session ids, MCP and messaging settings into every child CLI). Claude login comes from the keychain.
KEEP_ENV = ('PATH', 'HOME', 'USER', 'LOGNAME', 'TMPDIR', 'LANG', 'SHELL')

def child_env():
    env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
    env.update({k: v for k, v in dotenv_values(os.environ["DAYTONA_ENV_FILE"]).items() if v is not None})
    env.update(PYTHONPATH=str(ROOT), NO_COLOR='1')
    return env

def record(d):
    d['at'] = datetime.now(timezone.utc).isoformat()
    with (ROOT/'raw/launch-events.jsonl').open('a') as f: f.write(json.dumps(d) + '\n')
    print(json.dumps(d), flush=True)

def main():
    env = child_env()
    for name in sys.argv[1:]:
        cfg = ROOT/'configs'/(name + '.json')
        with (ROOT/'raw'/(name + '-launch-once.json')).open('x') as f:
            json.dump({'started_at': datetime.now(timezone.utc).isoformat(), 'arms_sha256': __import__('hashlib').sha256((ROOT/'agents.py').read_bytes()).hexdigest(),
                       'child_env_names': sorted(env)}, f)
        record({'event': 'harbor_start', 'name': name})
        with (ROOT/'raw'/(name + '-console.log')).open('w') as out:
            r = subprocess.run([HARBOR, 'run', '--config', str(cfg)], stdout=out, stderr=subprocess.STDOUT, env=env)
        res = list((ROOT/'raw/jobs'/name).glob('*/result.json'))
        data = json.loads(res[0].read_text()) if res else {}
        record({'event': 'harbor_end', 'name': name, 'returncode': r.returncode,
                'exception': (data.get('exception_info') or {}).get('exception_type'),
                'reward': ((data.get('verifier_result') or {}).get('rewards') or {}).get('reward')})

if __name__ == '__main__': main()
