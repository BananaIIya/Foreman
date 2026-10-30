"""Prepare a pinned no-model checkpoint replay for a finalized frozen trial."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from v32_runtime.replay import ROOT, validate_replay


def prepare(snapshot_dir):
    folder = Path(snapshot_dir).resolve()
    snapshot = json.loads((folder/'snapshot.json').read_text())
    task = snapshot['provenance']['task']
    _, trial = validate_replay(folder, task, (ROOT/'tasks'/task/'instruction.md').read_text())
    match = re.fullmatch(r'hybrid-(v32|v33|v34|v35)-(.+)', trial.parent.name)
    if not match or match[2] != task:
        raise ValueError('Unsupported frozen source candidate or mismatched task')
    frozen = json.loads((ROOT/'raw/candidates'/match[1]/'manifest.json').read_text())
    for relative, digest in frozen['source_sha256'].items():
        if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest() != digest:
            raise ValueError('Frozen source changed: '+relative)
    name = 'transport-probe-checkpoint-'+match[1]+'-'+task+'-'+folder.name+'-adapter-r3'
    config = json.loads((ROOT/'configs'/(trial.parent.name+'.json')).read_text())
    config['job_name'] = name
    config['agents'] = [{'import_path': 'checkpoint_replay_adapter:CheckpointReplay',
                        'kwargs': {'snapshot_dir': str(folder),
                                   'snapshot_manifest_sha256': hashlib.sha256((folder/'snapshot.json').read_bytes()).hexdigest()}}]
    target = ROOT/'configs'/(name+'.json')
    if target.exists():
        if json.loads(target.read_text()) != config:
            raise ValueError('Replay config already exists with different contents')
    else:
        with target.open('x') as stream:
            json.dump(config, stream, indent=2)
            stream.write('\n')
    return name


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', required=True)
    args = parser.parse_args()
    print(json.dumps({'config': prepare(args.snapshot), 'launched': False, 'model_calls': 0}))
