"""Recognize empty worker-provider failure after a model-only planning phase."""
import json
from pathlib import Path
from trial_validity import result_for as previous_result_for


def planned_startup_failure(trial):
    phases = Path(trial) / 'agent/phases'
    requests = list(phases.glob('*/request.json'))
    if {p.parent.name for p in requests} != {'decision-plan', 'worker-discovery'}:
        return None
    plan = phases / 'decision-plan'
    request = json.loads((plan / 'request.json').read_text())
    if not request.get('no_tools') or not (plan / 'phase-result.json').exists():
        return None
    plan_result = json.loads((plan / 'phase-result.json').read_text())
    if plan_result.get('evidence', {}).get('command_count', 0):
        return None
    results = list((phases / 'worker-discovery').glob('rounds/*/phase-result.json'))
    if not results:
        return None
    rows = [json.loads(p.read_text()) for p in results]
    if all(r.get('status') == 'error' and r.get('native_error')
           and not r.get('evidence', {}).get('text', '').strip()
           and not r.get('evidence', {}).get('command_count', 0) for r in rows):
        return {'kind': 'provider_failed_before_any_worker_answer_after_planning',
                'native_errors': [r['native_error'] for r in rows],
                'planning_cost_usd': plan_result.get('usage', {}).get('cost_usd'),
                'worker_cost_unknown_is_not_zero': any(r.get('usage', {}).get('cost_usd') is None for r in rows)}
    return None


def result_for(name):
    result = previous_result_for(name)
    if result.get('result_path'):
        failure = planned_startup_failure(Path(result['result_path']).parent)
        if failure:
            result.update(status='invalid', raw_grader_reward=result.get('reward'), reward=None,
                          validity_failure=failure)
    return result
