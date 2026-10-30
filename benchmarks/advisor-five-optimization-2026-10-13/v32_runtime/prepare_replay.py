"""Prepare a distinct zero-model diagnostic job; never launch it automatically."""
import argparse
import hashlib
import json
from pathlib import Path

from .replay import ROOT, validate_replay


def prepare(snapshot_dir):
    snapshot_dir = Path(snapshot_dir).resolve()
    snapshot = json.loads((snapshot_dir/'snapshot.json').read_text())
    task = snapshot['provenance']['task']
    if task not in json.loads((ROOT/'manifest.json').read_text())['tasks']:
        raise ValueError('Task is outside the declared benchmark cohort')
    instruction = (ROOT/'tasks'/task/'instruction.md').read_text()
    _, trial = validate_replay(snapshot_dir, task, instruction)
    original_name = trial.parent.name
    if not original_name.startswith('hybrid-v32-'):
        raise ValueError('This diagnostic accepts only V32 source trials')
    name = 'transport-probe-checkpoint-' + original_name.removeprefix('hybrid-') + '-' + snapshot_dir.name
    config = json.loads((ROOT/'configs'/(original_name+'.json')).read_text())
    config['job_name'] = name
    config['agents'] = [{
        'import_path': 'v32_runtime.replay:CheckpointReplay',
        'kwargs': {
            'snapshot_dir': str(snapshot_dir),
            'snapshot_manifest_sha256': hashlib.sha256((snapshot_dir/'snapshot.json').read_bytes()).hexdigest(),
        },
    }]
    path = ROOT/'configs'/(name+'.json')
    if path.exists():
        if json.loads(path.read_text()) != config:
            raise ValueError('Existing replay configuration differs')
    else:
        with path.open('x') as stream:
            json.dump(config, stream, indent=2)
            stream.write('\n')
    return name


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', required=True)
    args = parser.parse_args()
    print(json.dumps({'config': prepare(args.snapshot), 'launched': False,
                      'model_calls': 0, 'kind': 'post_trial_diagnostic_only'}))
