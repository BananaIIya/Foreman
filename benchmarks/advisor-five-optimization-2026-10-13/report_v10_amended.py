"""Preserve the frozen report and annotate a verified terminated transport failure."""
import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def main():
    runpy.run_path(str(ROOT / 'report_v10.py'), run_name='__main__')
    result = json.loads((ROOT / 'summary-v10.json').read_text())
    failure = json.loads((ROOT / 'raw/v10-cad-transport-failure.json').read_text())
    assert failure['confirmed_all_prior_processes_exited']
    assert failure['cohort_exit_code'] == 130
    for row in result['tasks']:
        if row['task'] == 'cad-model':
            assert row['hybrid']['status'] == 'pending_or_running'
            row['hybrid'].update(status='invalid', validity_failure=failure, original_score=None, cost_usd=None)
    result['validity_amendment'] = 'CAD terminated after a provider failure and stuck log upload. Missing final Harbor result is not a live attempt; no original score was produced.'
    (ROOT / 'summary-v10-validity-amendment.json').write_text(json.dumps(result, indent=2) + '\n')
    print('Amendment: CAD invalid, unknown cost, no original score; raw evidence unchanged.')

if __name__ == '__main__':
    main()
