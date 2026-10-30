import unittest
from routing import Budget, choose_action, evidence_fingerprint, usage_cost
from hybrid_native import account_codex, public_evidence


class RoutingTest(unittest.TestCase):
    def test_same_native_usage_has_same_cost_across_provider_counter_conventions(self):
        codex = {'inputTokens': 12000, 'cachedInputTokens': 8000,
                 'cacheWriteInputTokens': 0, 'outputTokens': 1000, 'reasoningOutputTokens': 900}
        # Independent expected arithmetic; reasoning is not charged a second time.
        self.assertAlmostEqual(usage_cost('gpt-6.1-sol', codex, 'codex'), .008+.0008+.010)
        cursor = {'inputTokens': 4000, 'cacheReadTokens': 8000, 'cacheWriteTokens': 0, 'outputTokens': 1000}
        self.assertAlmostEqual(usage_cost('grok-4.7', cursor, 'cursor'), .008+.004+.006)

    def test_missing_cache_usage_cannot_be_reported_as_zero_cost(self):
        self.assertIsNone(usage_cost('gpt-6-luna', {'inputTokens': 1000, 'outputTokens': 20}, 'codex'))
        b = Budget(1); b.record(None)
        self.assertFalse(b.can_continue())
        self.assertEqual(b.advisor_allowance(), 0)

    def test_advisor_must_leave_room_for_worker_and_gate(self):
        b = Budget(1, spent=.45)
        self.assertAlmostEqual(b.advisor_allowance(), .10)
        b.record(.12)
        self.assertEqual(b.advisor_allowance(), 0)
        self.assertTrue(b.can_continue())

    def test_second_self_check_escalates_without_hidden_score(self):
        d = dict(action='self_check', issue='invariant fails', evidence='local check output', paths=['impl.py'])
        self.assertEqual(choose_action(d, 'delivery', Budget(1, self_checks=1)), 'advisor')

    def test_unchanged_advisor_request_is_not_paid_twice(self):
        d = dict(action='advisor', issue='invariant fails', evidence='local check output', paths=['impl.py'])
        self.assertEqual(choose_action(d, 'delivery', Budget(1), [evidence_fingerprint(d)]), 'repeated_issue')

    def test_discovery_cannot_ship_without_implementation(self):
        self.assertEqual(choose_action({'action':'deliver'}, 'discovery', Budget(1)), 'continue')

    def test_resume_does_not_rebill_historical_or_duplicate_notifications(self):
        def event(turn, total, out):
            return {'method':'thread/tokenUsage/updated', 'params': {'threadId':'thread',
                'turnId':turn, 'tokenUsage': {'total': {'totalTokens':total}, 'last': {
                    'inputTokens':1000, 'cachedInputTokens':0, 'cacheWriteInputTokens':0, 'outputTokens':out}}}}
        old = event('old', 1100, 100); new = event('new', 2300, 200)
        result = account_codex([old,new,new], 'gpt-6.1-sol', turn_id='new')
        self.assertEqual(result['responses'], 1)
        self.assertAlmostEqual(result['cost_usd'], .004)

    def test_gate_receives_streamed_check_output_when_completion_has_none(self):
        events = [
            {'method':'item/commandExecution/outputDelta', 'params':{'itemId':'a', 'delta':'3 tests passed\n'}},
            {'method':'item/completed', 'params':{'item':{'id':'a', 'type':'commandExecution',
                 'command':'run checks', 'exitCode':0, 'aggregatedOutput':None}}},
        ]
        evidence = public_evidence(events)
        self.assertEqual(evidence['commands'][0]['output'], '3 tests passed\n')
        self.assertTrue(evidence['commands'][0]['output_available'])


if __name__ == '__main__':
    unittest.main()
