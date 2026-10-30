"""Freeze candidate V58: hash every runtime source it can import, its configs and assignment."""
import hashlib, json, re, shutil
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parent
out = ROOT / 'raw/candidates/v59'
assert not (out / 'manifest.json').exists(), 'V58 already frozen'
files = [p for p in ROOT.glob('*.py')] + [p for p in ROOT.glob('*.mjs')] + [ROOT / 'manifest.json']
for d in ROOT.glob('v*_runtime'):
    n = int(re.findall(r'\d+', d.name)[0])
    if n <= 31 or n == 59:
        files += [p for p in d.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
files += sorted((ROOT / 'configs').glob('hybrid-v59-*.json'))
src = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files))}
manifest = {
    'candidate': 'v59',
    'frozen_at': datetime.now(timezone.utc).isoformat(),
    'base': 'v31 (last same-version CAD+HOF success); V32-V57 changes not carried',
    'assignment': json.loads((ROOT / 'v59_runtime/assignment.json').read_text()),
    'source_sha256': src,
    'changes': [
        'Base V58 (Luna worker first attempt, 73% ceiling, bounded evidence copies, expert reviews once)',
        'Cursor advisor: one in-conversation resume after a provider transport drop (Premature close / stream CANCEL) instead of ending the phase; failed resume marks usage unknown, never crashes',
        'No forced 70/30 split of the first expert allowance (V58: split interrupted HOF mid-work and left CAD funds unused)',
    ],
    'rationale': 'Solo Sol cost is ~2/3 input re-reading; Grok worker input/cache pricing ($2/$0.50) is not cheaper than Sol ($2/$0.10), so only a Luna worker can substitute cheaply for exploration/setup.',
    'checks': {'v59_python_tests': 28, 'v59_node_resume_test': 'advisor_resume.test.mjs OK'},
    'benchmark_scope': 'User-selected statistical protocol (acceptance-policy-statistical-20261014.json): 3 runs per task for CAD, HOF, SATB, WAL; solo baselines extended to 3 runs each. ATRX excluded.',
    'limitations': ['Native response boundaries can overshoot targets; all usage counts',
                    'Exactly 3 runs per task; no extra resampling'],
}
out.mkdir(parents=True, exist_ok=True)
shutil.copytree(ROOT / 'v59_runtime', out / 'source', ignore=shutil.ignore_patterns('__pycache__'))
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(len(src), 'files frozen')
