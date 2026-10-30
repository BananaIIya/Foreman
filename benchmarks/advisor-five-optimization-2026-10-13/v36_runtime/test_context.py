import unittest
from .context import independent_request


class ContextTests(unittest.TestCase):
    def test_first_intervention_unchanged(self):
        payload = {'original_task': 'Do the whole task', 'cost_target_usd': .03}
        result, receipt = independent_request(payload, None)
        self.assertEqual(result, payload)
        self.assertIsNone(receipt)

    def test_fresh_context_keeps_failures_scope_and_budget(self):
        payload = {'original_task': 'Do the whole task', 'cost_target_usd': .03,
                   'worker_evidence': {'text': 'Failed to validate', 'checks': [{'exit_code': 1}]},
                   'expert_progress': {'required_work': 'Core requirement unfinished'},
                   'corrective_focus': {'primary_uncertainty': 'Unsupported assumption'},
                   'delivery_contract': {'token': 'task-token'},
                   'original_image_attachments': [{'sha256': 'original-image'}]}
        result, receipt = independent_request(payload, 'old-thread')
        self.assertEqual({k: result[k] for k in payload}, payload)
        self.assertEqual(receipt['prior_thread_id'], 'old-thread')
        self.assertFalse(receipt['model_call_added'])
        self.assertFalse(receipt['budget_reset'])
        self.assertFalse(receipt['grader_feedback'])
        result['worker_evidence']['checks'][0]['exit_code'] = 0
        self.assertEqual(payload['worker_evidence']['checks'][0]['exit_code'], 1)

    def test_rejects_inaccessible_context_and_missing_original(self):
        for payload in ({}, {'original_task': ''}, {'original_task': 'task',
            'worker_evidence': {'same_native_thread_reference': {'thread_id': 'old'}}}):
            with self.assertRaises(ValueError):
                independent_request(payload, 'old-thread')
