"""Build Harbor task directories for selected Commit0 libraries.

Each image clones commit-0/<lib> at its stubbed base_commit and deletes .git in the same layer (no reference
history in the image), installs dependencies, then makes pip offline. The verifier restores the original test
files before running them (agents cannot pass by editing tests) and scores the fraction of tests passed.
"""
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LIBS = ['simpy', 'tinydb', 'voluptuous']
ROWS = {r['repo'].split('/')[1]: r for r in json.loads((ROOT/'raw/commit0_rows.json').read_text())}

INSTRUCTION = """# Implement the `{lib}` Python library

The repository in `/app` is the `{lib}` library with its implementation removed: in `/app/{src}` function and method bodies were replaced with placeholders, while signatures, classes and docstrings remain. Implement the library so that it behaves as documented and its test suite passes.

- The tests are in `/app/{tests}` and run with `{cmd}` from `/app`. Do not modify the tests; the original test files are restored before grading.
- Write the implementation yourself. Do not download, copy or install the original library (or a fork of it) from the internet or any package index.
- Grading is the fraction of the test suite that passes.
"""

TEST_SH = """#!/bin/bash
set -uo pipefail
cd /app
mkdir -p /logs/verifier
# Restore the original tests, then run the suite.
rm -rf /app/{tests}
mkdir -p /app/{tests}
cp -a /tests/original_tests/. /app/{tests}/
tar -czf /logs/verifier/submission.tgz -C /app {src} 2>/dev/null || true
timeout 1500 {cmd} {tests} -p no:cacheprovider --json-report --json-report-file=/logs/verifier/report.json -q > /logs/verifier/pytest.txt 2>&1
python3 - <<'PY'
import json
try:
    s = json.load(open('/logs/verifier/report.json'))['summary']
except Exception:
    s = {{}}
total = s.get('total') or 0
passed = s.get('passed') or 0
reward = passed / total if total else 0.0
open('/logs/verifier/reward.txt', 'w').write(f'{{reward:.6f}}\\n')
print('passed', passed, 'total', total, 'reward', reward)
PY
"""


def dockerfile(r):
    s, lib = r['setup'], r['repo']
    # No editable install (it fails in the Daytona builder); the libraries have no runtime dependencies,
    # so the source tree is put on PYTHONPATH instead.
    pip = []
    for req in (s.get('packages') or []):
        pip.append(f'pip install --no-cache-dir -r {req}')
    if s.get('pip_packages'):
        pip.append('pip install --no-cache-dir ' + ' '.join(f"'{p}'" for p in s['pip_packages']))
    pip.append("pip install --no-cache-dir 'pytest<8.4' pytest-json-report")
    return f"""FROM python:{s['python']}
WORKDIR /app
# Snapshot of the stubbed base commit exported locally without .git (no reference history in the image).
COPY app/ /app/
ENV PYTHONPATH={'/app/' + r['src_dir'].rstrip('/').rsplit('/', 1)[0] if '/' in r['src_dir'].rstrip('/') else '/app'}
RUN {' && '.join(pip)}
# No package index after the build: the original library cannot be pip-installed by the agent.
RUN printf '[global]\\nno-index = true\\n' > /etc/pip.conf
"""


def build(lib):
    r = ROWS[lib]
    src, tests, cmd = r['src_dir'].rstrip('/'), r['test']['test_dir'].rstrip('/'), r['test']['test_cmd']
    d = ROOT/'tasks'/f'commit0-{lib}'
    if d.exists():
        shutil.rmtree(d)
    (d/'environment').mkdir(parents=True)
    (d/'tests'/'original_tests').mkdir(parents=True)
    (d/'instruction.md').write_text(INSTRUCTION.format(lib=lib, src=src, tests=tests, cmd=cmd))
    (d/'environment'/'Dockerfile').write_text(dockerfile(r))
    snap = subprocess.run(['git', '-C', str(ROOT/'raw/repos'/lib), 'archive', r['base_commit']], capture_output=True, check=True).stdout
    (d/'environment'/'app').mkdir()
    subprocess.run(['tar', '-x', '-C', str(d/'environment'/'app')], input=snap, check=True)
    (d/'tests'/'test.sh').write_text(TEST_SH.format(tests=tests, src=src, cmd=cmd))
    (d/'tests'/'test.sh').chmod(0o755)
    # Copy the original test directory at the base commit (identical to the reference tests in Commit0).
    archive = subprocess.run(['git', '-C', str(ROOT/'raw/repos'/lib), 'archive', r['base_commit'], tests],
                             capture_output=True, check=True).stdout
    subprocess.run(['tar', '-x', '-C', str(d/'tests'/'original_tests'), '--strip-components', str(len(tests.split('/')))],
                   input=archive, check=True)
    (d/'task.toml').write_text(f"""version = "1.0"

[task]
name = "commit0/{lib}"

[metadata]
source = "commit0"
base_commit = "{r['base_commit']}"
reference_commit = "{r['reference_commit']}"

[verifier]
timeout_sec = 1800.0

[agent]
timeout_sec = 5400.0

[environment]
build_timeout_sec = 1800.0
cpus = 2
memory_mb = 4096
storage_mb = 10240
gpus = 0
allow_internet = true
""")
    return d


if __name__ == '__main__':
    for lib in LIBS:
        d = build(lib)
        n = len(list((d/'tests'/'original_tests').rglob('test_*.py')))
        print(lib, d, 'original test files:', n)
