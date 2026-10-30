"""Behavioral tests: optional useful discovery; no fabricated model result."""
import unittest
from . import test_flow as fixtures

class InitialRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_specialist_has_no_worker_fee_or_fabricated_observation(self):
        f=fixtures.FlowTests();c=f.setup_flow(['advisor','deliver'],[fixtures.phase(.3,'{"status":"completed"}')])
        r=await c.run('Entire original task')
        f.worker.assert_not_awaited()
        self.assertEqual([p['role'] for p in r['phases']],['decision','advisor','decision'])
        self.assertAlmostEqual(r['cost_usd'],.302)
        first=f.gate.await_args_list[0].args[1]
        self.assertEqual(first['stage'],'initial_routing')
        self.assertEqual(first['worker_evidence']['observation_status'],'no_worker_called')
        self.assertEqual(first['original_task'],'Entire original task')
        self.assertNotIn('usage',first['worker_evidence'])

    async def test_requested_observation_runs_once_then_specialist_receives_actual_failure(self):
        failed=fixtures.phase(.02);failed['evidence']['commands']=[{'exit_code':1,'stderr':'missing original input'}]
        f=fixtures.FlowTests();c=f.setup_flow(['prepare','prepare','deliver'],[fixtures.phase(.3,'{"status":"completed"}')],[failed])
        r=await c.run('Entire original task')
        self.assertEqual(f.worker.await_count,1)
        self.assertEqual(r['preparations'],1)
        self.assertAlmostEqual(r['cost_usd'],.323)
        args=f.worker.await_args.args
        self.assertEqual(args[0],'worker-discovery');self.assertLessEqual(args[3],.04)
        self.assertEqual(args[1]['original_task'],'Entire original task')
        self.assertEqual(args[1]['preparation_contract']['action'],'prepare')
        self.assertEqual(f.expert.await_args.args[1]['worker_evidence']['commands'],failed['evidence']['commands'])

    async def test_initial_deliver_or_self_check_cannot_claim_unobserved_completion(self):
        for action in ['deliver','self_check']:
            f=fixtures.FlowTests();c=f.setup_flow([action,'deliver'],[fixtures.phase(.3,'{"status":"completed"}')])
            r=await c.run('Original requirement')
            self.assertEqual(r['advisor_calls'],1);f.worker.assert_not_awaited()

    async def test_empty_preparation_does_not_spend_worker_call(self):
        f=fixtures.FlowTests();c=f.setup_flow([], [fixtures.phase(.3,'{"status":"completed"}')])
        empty=fixtures.decision('prepare');empty['decision']['paths']=[]
        f.gate.side_effect=[empty,fixtures.decision('deliver')]
        r=await c.run('Original requirement')
        self.assertEqual(r['preparations'],0);f.worker.assert_not_awaited()

    async def test_unknown_routing_fee_halts_before_any_worker_or_specialist(self):
        f=fixtures.FlowTests();c=f.setup_flow([],[])
        d=fixtures.decision('advisor');d['usage']['cost_usd']=None
        f.gate.side_effect=[d]
        r=await c.run('Original requirement')
        self.assertIsNone(r['cost_usd']);f.worker.assert_not_awaited();f.expert.assert_not_awaited()

    async def test_discovery_overshoot_is_charged_without_further_model(self):
        f=fixtures.FlowTests();c=f.setup_flow(['prepare'],[],[fixtures.phase(.8)])
        r=await c.run('Original requirement')
        self.assertAlmostEqual(r['cost_usd'],.801)
        self.assertEqual(r['terminal_reason'],'budget_exhausted');f.expert.assert_not_awaited()

if __name__=='__main__':unittest.main()
