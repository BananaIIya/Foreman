"""Execute explicit expert delivery instructions; no model or scorer calls."""
import json
from pathlib import Path, PurePosixPath
from v8_runtime.execution import execute_commands


def validate_manifest(value, token):
    if not isinstance(value, dict) or set(value) != {'version', 'token', 'commands', 'check_commands', 'artifact_paths'}:
        raise ValueError('Delivery manifest fields do not match the protocol')
    if type(value['version']) is not int or value['version'] != 1 or not isinstance(token, str) or not token or value['token'] != token:
        raise ValueError('Delivery manifest belongs to a different intervention')
    for key in ['commands', 'check_commands']:
        commands = value[key]
        if not isinstance(commands, list) or not 1 <= len(commands) <= 4:
            raise ValueError('Delivery and check commands each need one to four entries')
        if any(not isinstance(c, str) or not c.strip() or len(c) > 20000 for c in commands):
            raise ValueError('Invalid delivery command')
    paths = value['artifact_paths']
    if not isinstance(paths, list) or not 1 <= len(paths) <= 12:
        raise ValueError('Expected bounded artifact paths')
    for path in paths:
        if not isinstance(path, str):
            raise ValueError('Artifact path must be a string')
        p = PurePosixPath(path)
        if not path.startswith('/app/') or '..' in p.parts or path.endswith('/'):
            raise ValueError('Artifact path must remain in the task workspace')
    return value


async def execute_delivery(folder, manifest, token, run_command, expert_evidence):
    validate_manifest(manifest, token)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    # An uncertain interrupted execution must never be replayed automatically.
    with (folder / 'delivery-started.json').open('x') as f:
        json.dump({'token': token, 'manifest': manifest, 'no_automatic_replay': True}, f)
    patch = {'status': 'patch', 'setup_commands': manifest['commands'],
             'validation_commands': manifest['check_commands'], 'files': [],
             'evidence_paths': manifest['artifact_paths'], 'requested_evidence': [],
             'handoff': 'Literal routine delivery and original-requirement checks supplied by this intervention.'}
    result = await execute_commands(patch, run_command)
    result['execution']['delivery_token'] = token
    result['execution']['expert_evidence'] = expert_evidence
    result['evidence']['text'] = json.dumps(result['execution'], ensure_ascii=False)
    (folder / 'execution-result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result
