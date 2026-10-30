"""A fresh corrective expert thread with explicit current-task evidence."""
import copy
import hashlib
import json


def independent_request(payload, prior_thread_id):
    if not prior_thread_id:
        return payload, None
    if not isinstance(payload.get('original_task'), str) or not payload['original_task'].strip():
        raise ValueError('Fresh expert requires the complete original assignment')
    if 'same_native_thread_reference' in json.dumps(payload):
        raise ValueError('A fresh thread cannot resolve a prior-thread evidence reference')
    result = copy.deepcopy(payload)
    result['independent_review'] = {
        'context': 'New native model thread, same current-task workspace and original inputs.',
        'prior_work': 'Supplied reports are claims to inspect, not authoritative conclusions. Actual failed checks remain failures.',
        'objective': 'Reassess the unsupported core assumption against original inputs before choosing a correction. Reuse supported implementation; repair the original deliverable.',
        'constraints': 'Existing remaining budget and call limit apply. No hidden grader, historical answer, or automatic retry.'}
    receipt = {'strategy': 'fresh_corrective_thread_same_workspace',
               'prior_thread_id': prior_thread_id,
               'original_task_sha256': hashlib.sha256(payload['original_task'].encode()).hexdigest(),
               'previous_evidence_preserved': True, 'model_call_added': False,
               'budget_reset': False, 'grader_feedback': False}
    return result, receipt
