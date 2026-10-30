"""Freeze candidate V58: hash every runtime source it can import, its configs and assignment."""
import hashlib, json, re, shutil
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parent
out = ROOT / 'raw/candidates/v64'
assert not (out / 'manifest.json').exists(), 'V58 already frozen'
files = [p for p in ROOT.glob('*.py')] + [p for p in ROOT.glob('*.mjs')] + [ROOT / 'manifest.json']
for d in ROOT.glob('v*_runtime'):
    n = int(re.findall(r'\d+', d.name)[0])
    if n <= 31 or n in (60, 64):  # V64 imports v60_runtime.remote
        files += [p for p in d.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
files += sorted((ROOT / 'configs').glob('*-v64-*.json'))
src = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files))}
assert any(k.startswith('v64_runtime/') for k in src) and any(k.startswith('v60_runtime/') for k in src) and not any(k.startswith('v63_runtime/') for k in src), 'wrong runtime frozen'
manifest = {
    'candidate': 'v64',
    'frozen_at': datetime.now(timezone.utc).isoformat(),
    'base': 'v31 (last same-version CAD+HOF success); V32-V57 changes not carried',
    'assignment': json.loads((ROOT / 'v64_runtime/assignment.json').read_text()),
    'source_sha256': src,
    'changes': [
        'V63 official advisor pattern with the executor changed from GPT-6 Luna to Claude Haiku 5.5 (Claude Agent SDK on the host)',
        'Haiku built-in tools disabled; only bash and view_image (run in the Daytona container via the isolated V60 relay) plus advisor',
        'User/global Claude settings not loaded; native Claude Code advisor disabled (CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1); server tool calls recorded',
        'Advisor unchanged from V63: Sol high, no tools, sees executor transcript, at most 3 calls, final-review nudge',
    ],
    'rationale': 'Official advisor claims compare executor+advisor against the executor alone; this measures that on the five tasks.',
    'checks': {'v64_python_tests': 12, 'sdk_probe': 'custom tools only, no server tool calls, only claude-haiku-5-5 in model usage, cumulative session cost'},
    'benchmark_scope': 'One round: HaikuWithAdvisor and HaikuSolo once each on all five tasks.',
    'limitations': ['Native response boundaries can overshoot targets; all usage counts',
                    'Exactly 3 runs per task; no extra resampling'],
}
out.mkdir(parents=True, exist_ok=True)
shutil.copytree(ROOT / 'v64_runtime', out / 'source', ignore=shutil.ignore_patterns('__pycache__'))
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(len(src), 'files frozen')
