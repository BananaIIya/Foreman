"""Freeze candidate V58: hash every runtime source it can import, its configs and assignment."""
import hashlib, json, re, shutil
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parent
out = ROOT / 'raw/candidates/v61'
assert not (out / 'manifest.json').exists(), 'V58 already frozen'
files = [p for p in ROOT.glob('*.py')] + [p for p in ROOT.glob('*.mjs')] + [ROOT / 'manifest.json']
for d in ROOT.glob('v*_runtime'):
    n = int(re.findall(r'\d+', d.name)[0])
    if n <= 31 or n == 61:
        files += [p for p in d.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
files += sorted((ROOT / 'configs').glob('hybrid-v61-*.json'))
src = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files))}
assert any(k.startswith('v61_runtime/') for k in src) and not any(k.startswith('v60_runtime/') for k in src), 'wrong runtime frozen'
manifest = {
    'candidate': 'v61',
    'frozen_at': datetime.now(timezone.utc).isoformat(),
    'base': 'v31 (last same-version CAD+HOF success); V32-V57 changes not carried',
    'assignment': json.loads((ROOT / 'v61_runtime/assignment.json').read_text()),
    'source_sha256': src,
    'changes': [
        'Base V60 (Luna worker groundwork in /app/worker, isolated controller interpreters, Cursor transport resume, bounded evidence copies)',
        'TRADEOFF MEASUREMENT, not acceptance: whole-trial hard ceiling 88% of original solo (was 73%)',
        'Expert framing: removed the stop-before-allowance checkpoint instruction; expert told to work as if solving alone and verify fully before completion',
        'Mid-turn 75% budget steer to the Sol expert disabled; hard native budget stop unchanged',
    ],
    'rationale': 'Solo Sol cost is ~2/3 input re-reading; Grok worker input/cache pricing ($2/$0.50) is not cheaper than Sol ($2/$0.10), so only a Luna worker can substitute cheaply for exploration/setup.',
    'checks': {'v61_python_tests': 31, 'v61_node_resume_test': 'advisor_resume.test.mjs OK'},
    'benchmark_scope': 'Tradeoff measurement: one run each on cad-model, hof-topology-interpenetration, satb-audio-transcription (Sol-advisor tasks). Hypothesis: accuracy recovers when the expert is not truncated or told to stop early. WAL/ATRX excluded.',
    'limitations': ['Native response boundaries can overshoot targets; all usage counts',
                    'Exactly 3 runs per task; no extra resampling'],
}
out.mkdir(parents=True, exist_ok=True)
shutil.copytree(ROOT / 'v61_runtime', out / 'source', ignore=shutil.ignore_patterns('__pycache__'))
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(len(src), 'files frozen')
