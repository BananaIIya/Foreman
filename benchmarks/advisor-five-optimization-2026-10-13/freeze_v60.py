"""Freeze candidate V58: hash every runtime source it can import, its configs and assignment."""
import hashlib, json, re, shutil
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parent
out = ROOT / 'raw/candidates/v60'
assert not (out / 'manifest.json').exists(), 'V58 already frozen'
files = [p for p in ROOT.glob('*.py')] + [p for p in ROOT.glob('*.mjs')] + [ROOT / 'manifest.json']
for d in ROOT.glob('v*_runtime'):
    n = int(re.findall(r'\d+', d.name)[0])
    if n <= 31 or n == 60:
        files += [p for p in d.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
files += sorted((ROOT / 'configs').glob('hybrid-v60-*.json'))
src = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files))}
assert any(k.startswith('v60_runtime/') for k in src) and not any(k.startswith('v59_runtime/') for k in src), 'wrong runtime frozen'
manifest = {
    'candidate': 'v60',
    'frozen_at': datetime.now(timezone.utc).isoformat(),
    'base': 'v31 (last same-version CAD+HOF success); V32-V57 changes not carried',
    'assignment': json.loads((ROOT / 'v60_runtime/assignment.json').read_text()),
    'source_sha256': src,
    'changes': [
        'Base V59 (Luna worker groundwork, 73% ceiling, bounded evidence copies, Cursor transport resume, unsplit first expert allowance)',
        'Controller-owned Python wrappers (environment metadata, remote command relay) run with python3 -I so workspace files cannot shadow stdlib modules (V59 HOF r1 infrastructure failure)',
        'Design choice: worker confined to /app/worker/ and never writes final required outputs or edits pre-existing task files; the expert produces the final artifact. Motivation: removes the shadowing bug class and makes the final artifact expert-owned. Observed symptom (not a proven cause): in V58/V59 CAD the expert patched a worker full draft and declared completion early (~$0.09).',
    ],
    'rationale': 'Solo Sol cost is ~2/3 input re-reading; Grok worker input/cache pricing ($2/$0.50) is not cheaper than Sol ($2/$0.10), so only a Luna worker can substitute cheaply for exploration/setup.',
    'checks': {'v60_python_tests': 31, 'v60_node_resume_test': 'advisor_resume.test.mjs OK'},
    'benchmark_scope': 'User-selected statistical protocol (acceptance-policy-statistical-20261014.json): 3 runs per task for CAD, HOF, SATB, WAL; solo baselines extended to 3 runs each. ATRX excluded.',
    'limitations': ['Native response boundaries can overshoot targets; all usage counts',
                    'Exactly 3 runs per task; no extra resampling'],
}
out.mkdir(parents=True, exist_ok=True)
shutil.copytree(ROOT / 'v60_runtime', out / 'source', ignore=shutil.ignore_patterns('__pycache__'))
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(len(src), 'files frozen')
