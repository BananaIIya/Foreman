#!/bin/bash
set -uo pipefail
cd /app
mkdir -p /logs/verifier
# Restore the original tests, then run the suite.
rm -rf /app/voluptuous/tests
mkdir -p /app/voluptuous/tests
cp -a /tests/original_tests/. /app/voluptuous/tests/
tar -czf /logs/verifier/submission.tgz -C /app voluptuous 2>/dev/null || true
timeout 1500 pytest voluptuous/tests -p no:cacheprovider --json-report --json-report-file=/logs/verifier/report.json -q > /logs/verifier/pytest.txt 2>&1
python3 - <<'PY'
import json
try:
    s = json.load(open('/logs/verifier/report.json'))['summary']
except Exception:
    s = {}
total = s.get('total') or 0
passed = s.get('passed') or 0
reward = passed / total if total else 0.0
open('/logs/verifier/reward.txt', 'w').write(f'{reward:.6f}\n')
print('passed', passed, 'total', total, 'reward', reward)
PY
