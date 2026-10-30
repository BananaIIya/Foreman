"""Separate an empty provider startup failure from a scored model answer."""
import json
from pathlib import Path
from report import result_for as unqualified_result_for


def startup_provider_failure(trial):
    phases=Path(trial)/'agent/phases'
    requests=list(phases.glob('*/request.json'))
    if len(requests)!=1 or requests[0].parent.name!='worker-discovery':
        return None
    results=list(requests[0].parent.glob('rounds/*/phase-result.json'))
    if not results:
        return None
    rows=[json.loads(p.read_text()) for p in results]
    if all(r.get('status')=='error' and r.get('native_error')
           and not r.get('evidence',{}).get('text','').strip()
           and not r.get('evidence',{}).get('command_count',0) for r in rows):
        return {'kind':'provider_failed_before_any_worker_answer',
                'native_errors':[r['native_error'] for r in rows]}
    return None


def result_for(name):
    result=unqualified_result_for(name)
    if result.get('result_path'):
        failure=startup_provider_failure(Path(result['result_path']).parent)
        if failure:
            result.update(status='invalid',raw_grader_reward=result.get('reward'),reward=None,
                          validity_failure=failure)
    return result
