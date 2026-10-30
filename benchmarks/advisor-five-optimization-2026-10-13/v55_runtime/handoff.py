"""Bind a late worker work order to current expert files, not a prose hint."""
import hashlib
import json
from pathlib import PurePosixPath


def app_path(value):
    if not isinstance(value, str):
        raise ValueError('path must be text')
    p = PurePosixPath(value)
    if not value.startswith('/app/') or '..' in p.parts or any(x in p.parts for x in ('.git', '.cursor', '.agents')):
        raise ValueError('path must remain in the task workspace')
    return value


def validate_manifest(data, contract):
    required = {'version', 'token', 'phase', 'status', 'inputs', 'input_contract',
                'worker_task', 'required_outputs', 'acceptance_checks',
                'specialist_evidence', 'unresolved_specialist_questions'}
    if not isinstance(data, dict) or set(data) != required:
        raise ValueError('invalid handoff fields')
    if data['version'] != 1 or data['token'] != contract['token'] or data['phase'] != contract['phase'] or data['status'] != 'ready':
        raise ValueError('stale or unfinished handoff')
    if data['unresolved_specialist_questions'] != []:
        raise ValueError('unresolved specialist work cannot be delegated')
    for field, limit in [('input_contract', 4000), ('worker_task', 2500)]:
        if not isinstance(data[field], str) or not data[field].strip() or len(data[field]) > limit:
            raise ValueError('missing or excessive ' + field)
    for field in ['required_outputs', 'acceptance_checks', 'specialist_evidence']:
        values = data[field]
        if not isinstance(values, list) or not 1 <= len(values) <= 8 or any(not isinstance(x, str) or not x.strip() or len(x) > 1500 for x in values):
            raise ValueError('invalid ' + field)
    for path in data['required_outputs']:
        app_path(path)
    inputs = data['inputs']
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 8:
        raise ValueError('concrete input files required')
    seen = set()
    for item in inputs:
        if not isinstance(item, dict) or set(item) != {'path', 'sha256', 'role'}:
            raise ValueError('invalid input record')
        path = app_path(item['path'])
        if path in seen or path == contract['manifest_path']:
            raise ValueError('duplicate or self-referential input')
        seen.add(path)
        if not isinstance(item['role'], str) or not item['role'].strip() or len(item['role']) > 500:
            raise ValueError('missing input role')
        digest = item['sha256']
        if not isinstance(digest, str) or len(digest) != 64 or any(x not in '0123456789abcdef' for x in digest):
            raise ValueError('invalid input hash')
    return data


async def collect_handoff(contract, directory, download_file):
    directory.mkdir(parents=True, exist_ok=True)
    receipt = {'status': 'unavailable', 'model_calls': 0, 'correctness_proven': False,
               'phase': contract['phase'], 'contract': contract}
    try:
        local = directory / 'manifest.json'
        await download_file(contract['manifest_path'], local)
        raw = local.read_bytes()
        if len(raw) > 32768:
            raise ValueError('handoff exceeds 32 KiB')
        data = validate_manifest(json.loads(raw), contract)
        actual = []
        for i, item in enumerate(data['inputs']):
            local = directory / ('input-' + str(i))
            await download_file(item['path'], local)
            digest = hashlib.sha256(local.read_bytes()).hexdigest()
            if digest != item['sha256']:
                raise ValueError('input changed or hash is false')
            actual.append({'path': item['path'], 'sha256': digest, 'bytes': local.stat().st_size})
        receipt.update(status='available', manifest=data, manifest_sha256=hashlib.sha256(raw).hexdigest(), inputs=actual)
    except Exception as exc:
        receipt.update(status='invalid' if isinstance(exc, (ValueError, TypeError, KeyError)) else 'unavailable', error_type=type(exc).__name__)
    (directory / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt
