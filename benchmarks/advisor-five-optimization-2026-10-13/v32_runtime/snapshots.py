"""Full delivery bytes for post-trial analysis, never solver or grader feedback."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

MAX_FILE_BYTES = 64 * 1024 * 1024


def valid_source(source):
    if not isinstance(source, str):
        return False
    path = PurePosixPath(source)
    return (source.startswith('/app/') and '..' not in path.parts
            and not any(part in path.parts for part in
                        ('.git', '.codex', '.cursor', '.agents', '.aws', 'credentials')))


async def capture_snapshot(folder, sources, download, provenance):
    """Snapshot only explicitly declared delivery files after literal execution.

    Download failures are recorded as incomplete diagnostic evidence. They do not
    fabricate artifacts, trigger another model call, or change execution results.
    A prior snapshot directory is never overwritten or replayed.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    rows = []
    seen = set()
    for source in sources:
        if isinstance(source,str):
            if source in seen:
                continue
            seen.add(source)
        row = {'source_path': source}
        rows.append(row)
        if not valid_source(source):
            row.update(status='rejected_path')
            continue
        if source.endswith('/'):
            row.update(status='unsupported_directory')
            continue
        name = hashlib.sha256(source.encode()).hexdigest() + '.artifact'
        target = folder / name
        partial = folder / (name + '.partial')
        try:
            await download(source, partial)
            size = partial.stat().st_size
            if size > MAX_FILE_BYTES:
                partial.unlink()
                row.update(status='too_large', size_bytes=size)
                continue
            digest = hashlib.sha256(partial.read_bytes()).hexdigest()
            partial.rename(target)
            row.update(status='captured', local_file=name,
                       size_bytes=size, sha256=digest)
        except Exception as exc:
            if partial.exists():
                partial.unlink()
            row.update(status='unavailable', error_type=type(exc).__name__)
    result = {
        'version': 1,
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'timing': 'after_advisor_and_optional_literal_delivery_before_next_routing_model',
        'provenance': provenance,
        'files': rows,
        'complete': bool(rows) and all(r['status'] == 'captured' for r in rows),
        'remote_workspace_modified': False,
        'model_calls': 0,
        'grader_calls': 0,
        'exposed_to_models': False,
    }
    with (folder / 'snapshot.json').open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    return result


def verify_snapshot(folder):
    """Return corruption problems separately from unavailable snapshot evidence."""
    folder = Path(folder)
    record = json.loads((folder / 'snapshot.json').read_text())
    problems = []
    if record.get('complete') != (bool(record['files']) and all(r['status']=='captured' for r in record['files'])):
        problems.append('Snapshot completeness contradicts file records')
    for field, expected in [('version', 1), ('model_calls', 0), ('grader_calls', 0),
                            ('remote_workspace_modified', False), ('exposed_to_models', False)]:
        if record.get(field) != expected:
            problems.append('Snapshot boundary drift: ' + field)
    for row in record['files']:
        if row['status'] != 'captured':
            continue
        source = row['source_path']
        expected = hashlib.sha256(source.encode()).hexdigest() + '.artifact'
        if not valid_source(source) or row.get('local_file') != expected:
            problems.append('Snapshot path identity mismatch')
            continue
        path = folder / expected
        if (not path.is_file() or path.stat().st_size != row['size_bytes']
                or hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']):
            problems.append('Snapshot bytes changed: ' + source)
    return problems
