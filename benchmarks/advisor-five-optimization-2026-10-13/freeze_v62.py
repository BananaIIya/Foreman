"""Freeze candidate V58: hash every runtime source it can import, its configs and assignment."""
import hashlib, json, re, shutil
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parent
out = ROOT / 'raw/candidates/v62'
assert not (out / 'manifest.json').exists(), 'V58 already frozen'
files = [p for p in ROOT.glob('*.py')] + [p for p in ROOT.glob('*.mjs')] + [ROOT / 'manifest.json']
for d in ROOT.glob('v*_runtime'):
    n = int(re.findall(r'\d+', d.name)[0])
    if n <= 31 or n == 62:
        files += [p for p in d.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
files += sorted((ROOT / 'configs').glob('*-v62-*.json'))
src = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files))}
assert any(k.startswith('v62_runtime/') for k in src) and not any(k.startswith('v61_runtime/') for k in src), 'wrong runtime frozen'
manifest = {
    'candidate': 'v62',
    'frozen_at': datetime.now(timezone.utc).isoformat(),
    'base': 'v31 (last same-version CAD+HOF success); V32-V57 changes not carried',
    'assignment': json.loads((ROOT / 'v62_runtime/assignment.json').read_text()),
    'source_sha256': src,
    'changes': [
        'New pattern (Anthropic advisor strategy): Luna medium executor does all work in its own native session; an `advisor` dynamic tool returns short advice from a no-tools advisor that sees the executor transcript',
        'Advisor: Sol high (CAD/HOF/SATB, one resumed no-tools thread, original images on first call) or Grok high (WAL/ATRX, no-tools Cursor SDK answer per call); at most 3 calls',
        'Executor guidance: call after orientation, when stuck, and for a final review; one controller nudge for a final review if no advice followed the last work',
        'Control arm LunaSolo: identical transport and model with no advisor and no extra instructions',
    ],
    'rationale': 'Official advisor claims compare executor+advisor against the executor alone; this measures that on the five tasks.',
    'checks': {'v62_python_tests': 9, 'protocol_probe': 'raw/preflight/v62-protocol-probe/result.json'},
    'benchmark_scope': 'Measurement: 3 runs each of LunaSolo and LunaWithAdvisor on all five tasks; compared with each other and with the existing advisor solo runs.',
    'limitations': ['Native response boundaries can overshoot targets; all usage counts',
                    'Exactly 3 runs per task; no extra resampling'],
}
out.mkdir(parents=True, exist_ok=True)
shutil.copytree(ROOT / 'v62_runtime', out / 'source', ignore=shutil.ignore_patterns('__pycache__'))
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(len(src), 'files frozen')
