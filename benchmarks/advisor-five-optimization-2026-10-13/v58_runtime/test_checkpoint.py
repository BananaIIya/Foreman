import unittest
# V58 expectations: 73% ceiling and no planner phase; same allowance formula as V31.
from .coordinator import Budget, Coordinator


class CheckpointBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def fixture(self, needs_repair=True, overshoot=0.):
        expert_calls, decision_contexts, emitted = [], [], {}
        async def planner(*args):
            return {'status': 'completed', 'usage': {'cost_usd': .001}, 'work_order': {'objective': 'Inspect original input'}}
        async def worker(*args):
            return {'status': 'completed', 'usage': {'cost_usd': .02}, 'evidence': {'text': 'Original input observed'}}
        async def decision(phase, context):
            decision_contexts.append(context)
            repair = len(expert_calls) == 1 and needs_repair
            action = 'advisor' if not expert_calls or repair else 'deliver'
            return {'status': 'completed', 'usage': {'cost_usd': .001}, 'decision': {
                'action': action, 'issue': 'Observed omitted invariant' if repair else 'Specialist inference needed',
                'evidence': context['worker_evidence']['text'], 'paths': ['/app/output'],
                'intervention': 'Resolve the observed original requirement', 'acceptance': 'Check original input'}}
        async def advisor(phase, payload):
            expert_calls.append(payload)
            return {'status': 'completed', 'usage': {'cost_usd': payload['cost_target_usd'] + overshoot if len(expert_calls)==1 else .04},
                    'evidence': {'text': 'Candidate has an observed omitted invariant' if len(expert_calls)==1 else 'Corrected invariant checked'}}
        async def finalizer(*args):
            return None
        c = Coordinator(.5, 'cursor/grok-4.7-low', worker, decision, advisor,
                        lambda name, value: emitted.update({name: value}), planner, finalizer)
        result = await c.run('Original task, with its original invariants')
        return result, expert_calls, decision_contexts, emitted

    async def test_midpoint_review_has_funded_correction(self):
        result, calls, gates, emitted = await self.fixture(overshoot=.005)
        self.assertEqual(len(calls), 2)
        self.assertAlmostEqual(calls[0]['cost_target_usd'], .2373)
        self.assertAlmostEqual(calls[0]['review_checkpoint']['correction_reserve_usd'], .1017)
        self.assertAlmostEqual(calls[1]['cost_target_usd'], .0957)
        self.assertEqual(gates[1]['worker_evidence']['text'], 'Candidate has an observed omitted invariant')
        self.assertEqual(calls[1]['worker_evidence'], gates[1]['worker_evidence'])
        self.assertAlmostEqual(result['cost_usd'], .3053)
        self.assertEqual(result['terminal_reason'], 'deliver')
        self.assertFalse(result['verifier_feedback'])
        self.assertEqual(emitted['checkpoint-budget-advisor-1']['kind'], 'focused_correction')

    async def test_success_does_not_spend_reserve_automatically(self):
        result, calls, _, _ = await self.fixture(needs_repair=False)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['terminal_reason'], 'deliver')
        self.assertAlmostEqual(result['cost_usd'], .2593)

    async def test_overshoot_is_charged_and_can_consume_reserve(self):
        result, calls, _, _ = await self.fixture(overshoot=.15)
        self.assertEqual(len(calls), 1)
        self.assertGreater(result['cost_usd'], result['target_ceiling_usd'])
        self.assertEqual(result['terminal_reason'], 'budget_exhausted_after_integration')
        self.assertAlmostEqual(result['cost_usd'], .4083)

    def test_no_allowance_for_unknown_usage_or_third_call(self):
        self.assertEqual(Budget(.5, usage_complete=False).advisor_allowance(), 0.)
        self.assertEqual(Budget(.5, advisor_calls=2).advisor_allowance(), 0.)
        self.assertAlmostEqual(Budget(.5, advisor_calls=1, spent=.3).advisor_allowance(), .045)


if __name__ == '__main__':
    unittest.main()
