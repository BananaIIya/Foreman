"""Freeze candidate V58: hash every runtime source it can import, its configs and assignment."""
import hashlib, json, re, shutil
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parent
out = ROOT / 'raw/candidates/v58'
assert not (out / 'manifest.json').exists(), 'V58 already frozen'
files = [p for p in ROOT.glob('*.py')] + [p for p in ROOT.glob('*.mjs')] + [ROOT / 'manifest.json']
for d in ROOT.glob('v*_runtime'):
    n = int(re.findall(r'\d+', d.name)[0])
    if n <= 31 or n == 58:
        files += [p for p in d.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
files += sorted((ROOT / 'configs').glob('hybrid-v58-*.json'))
src = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files))}
manifest = {
    'candidate': 'v58',
    'frozen_at': datetime.now(timezone.utc).isoformat(),
    'base': 'v31 (last same-version CAD+HOF success); V32-V57 changes not carried',
    'assignment': json.loads((ROOT / 'v58_runtime/assignment.json').read_text()),
    'source_sha256': src,
    'changes': [
        'Worker for CAD/HOF/SATB changed from cursor/grok-4.7-low to gpt-6-luna medium (WAL/ATRX already Luna); advisors and decision model unchanged',
        'No planner phase: the low-cost worker makes a real first attempt at the full original task with original images, capped at 6% of solo',
        'Expert always reviews once before delivery (worker-claimed completion is promoted to advisor)',
        'One bounded routine worker repair within 10% of solo',
        'Whole-trial ceiling 73% of verified-correct solo (was 70%)',
        'Prompt copies of command evidence bounded (6 commands, 600-char commands, 1200-char output tails); raw logs keep everything',
    ],
    'rationale': 'Solo Sol cost is ~2/3 input re-reading; Grok worker input/cache pricing ($2/$0.50) is not cheaper than Sol ($2/$0.10), so only a Luna worker can substitute cheaply for exploration/setup.',
    'checks': {'v58_unit_tests': 29},
    'benchmark_scope': 'One attempt each: cad-model, hof-topology-interpenetration, wal-recovery-ordering, satb-audio-transcription. ATRX excluded (no correct solo baseline; task reference conflict).',
    'limitations': ['Native response boundaries can overshoot targets; all usage counts',
                    'Single attempts; no resampling of the same candidate to fish for passes'],
}
out.mkdir(parents=True, exist_ok=True)
shutil.copytree(ROOT / 'v58_runtime', out / 'source', ignore=shutil.ignore_patterns('__pycache__'))
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(len(src), 'files frozen')
