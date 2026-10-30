import unittest
from unittest.mock import patch
from v33_runtime.test_focus import FocusTests
from .coordinator import Budget, Coordinator


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    def test_continuous_allowance_retains_whole_ceiling_and_review_reserve(self):
        b=Budget(.5,spent=.022)
        self.assertAlmostEqual(b.ceiling,.35)
        self.assertAlmostEqual(b.advisor_allowance(),.323)
        b.advisor_calls=1;b.spent=.15
        self.assertAlmostEqual(b.advisor_allowance(),.195)
        b.advisor_calls=2
        self.assertEqual(b.advisor_allowance(),0)
        b.advisor_calls=0;b.usage_complete=False
        self.assertEqual(b.advisor_allowance(),0)

    async def test_early_return_can_still_receive_grounded_correction(self):
        with patch('v33_runtime.test_focus.Coordinator',Coordinator):
            result,calls,gates,emits=await FocusTests().run_fixture()
        self.assertEqual(len(calls),2)
        self.assertAlmostEqual(calls[0]['cost_target_usd'],.323)
        self.assertEqual(calls[0]['review_checkpoint']['correction_reserve_usd'],0)
        self.assertAlmostEqual(result['cost_usd'],.104)
        self.assertIn('corrective_focus',calls[1])
        self.assertFalse(result['verifier_feedback'])

    def test_only_budget_paragraph_changes(self):
        from .policy import OLD,NEW,ADVISOR
        from v34_runtime import policy as old
        from . import policy as new
        self.assertEqual(ADVISOR.replace(NEW,OLD),old.ADVISOR)
        for field in ['PLAN','PLAN_SCHEMA','INITIAL_WORK','WORKER_SYSTEM','DECISION','DECISION_SCHEMA']:
            self.assertEqual(getattr(old,field),getattr(new,field))
