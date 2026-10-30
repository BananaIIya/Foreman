"""Post-hoc integrity check: similarity of each submitted source tree to the reference implementation.

Very high similarity can mean the model reproduced the original library (memorized or fetched it). This is a
reported limitation, not proof either way; libraries this popular are likely in training data.
"""
import difflib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ROWS = {r['repo'].split('/')[1]: r for r in json.loads((ROOT/'raw/commit0_rows.json').read_text())}


def reference_files(lib):
    r = ROWS[lib]
    src = r['src_dir'].rstrip('/')
    out = subprocess.run(['git', '-C', str(ROOT/'raw/repos'/lib), 'ls-tree', '-r', '--name-only', r['reference_commit'], '--', src],
                         capture_output=True, text=True, check=True).stdout.split()
    files = {}
    for f in out:
        if f.endswith('.py') and '/tests/' not in f:
            files[f] = subprocess.run(['git', '-C', str(ROOT/'raw/repos'/lib), 'show', f'{r["reference_commit"]}:{f}'],
                                      capture_output=True, text=True).stdout
    return files


def submission_files(job):
    tgz = next((ROOT/'raw/jobs'/job).glob('*/verifier/submission.tgz'), None)
    if not tgz:
        return None
    files = {}
    with tarfile.open(tgz) as t:
        for m in t.getmembers():
            if m.isfile() and m.name.endswith('.py') and '/tests/' not in m.name:
                files[m.name] = t.extractfile(m).read().decode('utf-8', 'replace')
    return files


def score(job, lib):
    ref, sub = reference_files(lib), submission_files(job)
    if sub is None:
        return None
    total = sum(len(v) for v in ref.values()) or 1
    weighted = 0.0
    per = {}
    for path, text in ref.items():
        s = sub.get(path, '')
        ratio = difflib.SequenceMatcher(None, s, text, autojunk=False).ratio() if s else 0.0
        per[path] = round(ratio, 3)
        weighted += ratio * len(text)
    return {'weighted_similarity': round(weighted / total, 3), 'per_file': per}


if __name__ == '__main__':
    for job in sys.argv[1:]:
        lib = job.rsplit('-', 1)[-1]
        print(job, json.dumps(score(job, lib)))
