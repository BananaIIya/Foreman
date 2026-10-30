import unittest
from unittest.mock import patch
from v33_runtime.test_focus import FocusTests
from v34_runtime.coordinator import Coordinator, Budget
from v36_runtime.context import independent_request


class ReviewBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_corrective_request_has_budget_and_full_new_context(self):
        with patch('v33_runtime.test_focus.Coordinator', Coordinator):
            result, calls, gates, emits = await FocusTests().run_fixture()
        self.assertEqual(len(calls), 2)
        self.assertAlmostEqual(calls[0]['cost_target_usd'], .323*.70)
        self.assertGreater(calls[0]['review_checkpoint']['correction_reserve_usd'], 0)
        self.assertGreater(calls[1]['cost_target_usd'], 0)
        second, receipt = independent_request(calls[1], 'first-thread')
        self.assertEqual(second['original_task'], calls[0]['original_task'])
        self.assertEqual(second['worker_evidence'], calls[1]['worker_evidence'])
        self.assertEqual(second['cost_target_usd'], calls[1]['cost_target_usd'])
        self.assertIn('corrective_focus', second)
        self.assertFalse(receipt['model_call_added'])
        self.assertAlmostEqual(result['cost_usd'], .104)
        self.assertFalse(result['verifier_feedback'])

    def test_whole_ceiling_and_call_limit_unchanged(self):
        b = Budget(.5, spent=.022)
        self.assertAlmostEqual(b.ceiling, .35)
        self.assertAlmostEqual(b.advisor_allowance(), .323*.70)
        b.advisor_calls = 1
        self.assertAlmostEqual(b.advisor_allowance(), .323)
        b.advisor_calls = 2
        self.assertEqual(b.advisor_allowance(), 0)
