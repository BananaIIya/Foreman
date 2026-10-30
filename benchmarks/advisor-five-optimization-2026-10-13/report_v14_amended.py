"""Keep the frozen report, supplement verified provider-startup validity."""
import json
import runpy
from pathlib import Path
from trial_validity_v2 import result_for

ROOT = Path(__file__).resolve().parent

if __name__ == '__main__':
    runpy.run_path(str(ROOT / 'report_v14.py'), run_name='__main__')
    summary = json.loads((ROOT / 'summary-v14.json').read_text())
    for row in summary['tasks']:
        amended = result_for('hybrid-v14-' + row['task'])
        if amended['status'] == 'invalid':
            row.update(hybrid=amended, qualifies=False)
    summary['validity_amendment'] = 'Empty provider startup failures after planning remain invalid; raw grader artifacts and all cost records are unchanged.'
    (ROOT / 'summary-v14-validity-amendment.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('Validity supplement saved; raw scores and frozen sources unchanged.')
