"""Retain all costs and classify checkpoint grading separately from model runs."""
import contextlib
import io
import json
from pathlib import Path

from expenses import main as previous_main
from v32_runtime.replay import replay_model_cost

ROOT = Path(__file__).resolve().parent


def main():
    with contextlib.redirect_stdout(io.StringIO()):
        previous_main()
    out = json.loads((ROOT/'expenses.json').read_text())
    for row in out['attempts']:
        if not row['name'].startswith('transport-probe-checkpoint-'):
            continue
        trial = Path(row['result_path']).parent
        cost = replay_model_cost(trial/'agent')
        row['category'] = 'checkpoint_grader_replay'
        row['is_solver_attempt'] = False
        if cost is not None:
            row.update(cost_usd=cost, known_cost_lower_bound_usd=cost,
                       model_cost_basis='Verified no-model artifact restore; infrastructure excluded')
    totals = {}
    for row in out['attempts']:
        total = totals.setdefault(row['category'],
            {'known_cost_usd': 0., 'unknown_or_pending_attempts': 0, 'attempts': 0})
        total['attempts'] += 1
        if row['cost_usd'] is None or not row['complete']:
            total['unknown_or_pending_attempts'] += 1
        total['known_cost_usd'] += row.get('known_cost_lower_bound_usd', row['cost_usd'] or 0.)
    out['totals'] = totals
    (ROOT/'expenses.json').write_text(json.dumps(out, indent=2)+'\n')
    print(json.dumps(totals))


if __name__ == '__main__':
    main()
