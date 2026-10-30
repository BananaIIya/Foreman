"""Freeze candidate V58: hash every runtime source it can import, its configs and assignment."""
import hashlib, json, re, shutil
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parent
out = ROOT / 'raw/candidates/v65'
assert not (out / 'manifest.json').exists(), 'V58 already frozen'
files = [p for p in ROOT.glob('*.py')] + [p for p in ROOT.glob('*.mjs')] + [ROOT / 'manifest.json']
for d in ROOT.glob('v*_runtime'):
    n = int(re.findall(r'\d+', d.name)[0])
    if n <= 31 or n in (60, 61, 65):  # V65 imports v60_runtime.remote and v61_runtime.native
        files += [p for p in d.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
files += sorted((ROOT / 'configs').glob('*-v65-*.json'))
src = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files))}
assert all(any(k.startswith(f'v{v}_runtime/') for k in src) for v in (60, 61, 65)) and not any(k.startswith(f'v{v}_runtime/') for k in src for v in (62, 63, 64)), 'wrong runtime frozen'
manifest = {
    'candidate': 'v65',
    'frozen_at': datetime.now(timezone.utc).isoformat(),
    'base': 'v31 (last same-version CAD+HOF success); V32-V57 changes not carried',
    'assignment': json.loads((ROOT / 'v65_runtime/assignment.json').read_text()),
    'source_sha256': src,
    'changes': [
        'Cheap-first routing: Haiku 5.5 attempts the task alone (V64 tools; native Claude advisor off), capped at 40% of the Sol solo mean ($0.20 for WAL/ATRX)',
        'Then Sol high opens a native session in the same workspace (V61 session, no early-stop framing, no mid-turn steer), verifies first and edits only on a concrete failed check, capped at 88% of the Sol solo mean ($0.60 for WAL/ATRX)',
        'Workspace sha256 snapshot before and diff after the Sol stage record whether Sol accepted without edits',
    ],
    'rationale': 'Official advisor claims compare executor+advisor against the executor alone; this measures that on the five tasks.',
    'checks': {'local_change_detection_test': 'modified/deleted/new files detected'},
    'benchmark_scope': 'Small experiment: one run per task on all five tasks.',
    'limitations': ['Native response boundaries can overshoot targets; all usage counts',
                    'Exactly 3 runs per task; no extra resampling'],
}
out.mkdir(parents=True, exist_ok=True)
shutil.copytree(ROOT / 'v65_runtime', out / 'source', ignore=shutil.ignore_patterns('__pycache__'))
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(len(src), 'files frozen')
