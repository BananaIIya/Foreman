"""Freeze candidate V58: hash every runtime source it can import, its configs and assignment."""
import hashlib, json, re, shutil
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parent
out = ROOT / 'raw/candidates/v63'
assert not (out / 'manifest.json').exists(), 'V58 already frozen'
files = [p for p in ROOT.glob('*.py')] + [p for p in ROOT.glob('*.mjs')] + [ROOT / 'manifest.json']
for d in ROOT.glob('v*_runtime'):
    n = int(re.findall(r'\d+', d.name)[0])
    if n <= 31 or n == 63:
        files += [p for p in d.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
files += sorted((ROOT / 'configs').glob('*-v63-*.json'))
src = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files))}
assert any(k.startswith('v63_runtime/') for k in src) and not any(k.startswith('v62_runtime/') for k in src), 'wrong runtime frozen'
manifest = {
    'candidate': 'v63',
    'frozen_at': datetime.now(timezone.utc).isoformat(),
    'base': 'v31 (last same-version CAD+HOF success); V32-V57 changes not carried',
    'assignment': json.loads((ROOT / 'v63_runtime/assignment.json').read_text()),
    'source_sha256': src,
    'changes': [
        'Base V62 official advisor pattern (Luna executor does all work; no-tools advisor sees its transcript, at most 3 calls, final-review nudge)',
        'Advisor is Sol high on every task (V62 WAL/ATRX Grok no-tools answers dropped with Cursor Premature close at ~170-200 s of reasoning)',
        'Advisor calls serialized under a lock with call numbers assigned at request time (V62 ATRX: an overlapping call hit FileExistsError)',
        'A consultation still running at executor close is recorded with unknown cost, never zero; failed calls keep their billed native usage',
    ],
    'rationale': 'Official advisor claims compare executor+advisor against the executor alone; this measures that on the five tasks.',
    'checks': {'v63_python_tests': 12},
    'benchmark_scope': 'Reruns the official arm on wal-recovery-ordering and atrx-vep-crispr (3 each); Luna-solo control arms come from V62 (identical transport).',
    'limitations': ['Native response boundaries can overshoot targets; all usage counts',
                    'Exactly 3 runs per task; no extra resampling'],
}
out.mkdir(parents=True, exist_ok=True)
shutil.copytree(ROOT / 'v63_runtime', out / 'source', ignore=shutil.ignore_patterns('__pycache__'))
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(len(src), 'files frozen')
