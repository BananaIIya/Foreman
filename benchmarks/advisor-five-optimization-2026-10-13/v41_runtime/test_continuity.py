import unittest
from unittest.mock import patch
from v33_runtime.test_focus import FocusTests
from .coordinator import Coordinator


class ContinuityTests(unittest.IsolatedAsyncioTestCase):
    async def test_continuous_budget_retains_new_target_and_total_charges(self):
        with patch('v33_runtime.test_focus.Coordinator', Coordinator):
            result, calls, gates, emits = await FocusTests().run_fixture()
        self.assertAlmostEqual(result['target_ceiling_usd'], .36)
        self.assertAlmostEqual(calls[0]['cost_target_usd'], .333)
        self.assertEqual(calls[0]['review_checkpoint']['correction_reserve_usd'], 0)
        self.assertEqual(len(calls), 2)
        self.assertAlmostEqual(result['cost_usd'], .104)
        self.assertFalse(result['verifier_feedback'])

    def test_only_advisor_allocation_paragraph_changes(self):
        from . import policy as new
        from v39_runtime import policy as old
        self.assertEqual(new.ADVISOR.replace(new.NEW, new.OLD), old.ADVISOR)
        for key in ('PLAN', 'PLAN_SCHEMA', 'INITIAL_WORK', 'WORKER_SYSTEM', 'DECISION', 'DECISION_SCHEMA'):
            self.assertEqual(getattr(new, key), getattr(old, key))
