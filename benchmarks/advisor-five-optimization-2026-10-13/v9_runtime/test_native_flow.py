import json,unittest
from types import SimpleNamespace
from .coordinator import Coordinator
from .policy import Budget
from .relay import CommandRelay

def result(cost,status='completed'):
    return {'status':status,'usage':{'cost_usd':cost},'evidence':{'text':'actual observed work','commands':[{'exit_code':0}]}}

class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def scenario(self,expert_cost=.12,expert_status='completed'):
        events=[];actions=iter(['advisor','deliver'])
        async def worker(phase,payload,policy,cap):
            events.append(('worker',phase));return result(.02)
        async def decision(phase,payload):
            events.append(('gate',phase,payload));return dict(result(.001),decision={'action':next(actions),'issue':'specialist uncertainty','evidence':'actual inputs','paths':['/app/input'],'intervention':'resolve the missing algorithm','acceptance':'original requirements'})
        async def advisor(phase,payload):
            events.append(('expert',phase,payload));return result(expert_cost,expert_status)
        return await Coordinator(.3,'cursor/grok-4.7-low',worker,decision,advisor,lambda *args:None).run('original task'),events

    async def test_native_completed_patch_goes_to_gate_without_worker_reconstruction(self):
        report,events=await self.scenario()
        self.assertEqual(report['terminal_reason'],'deliver')
        self.assertEqual(sum(x[0]=='worker' for x in events),1)
        self.assertEqual(report['integrations'],0)
        self.assertTrue(report['advisor_workspace_access'])
        self.assertFalse(report['advisor_code_applied_by_controller'])
        self.assertAlmostEqual(report['cost_usd'],.142)
        expert=next(x for x in events if x[0]=='expert')
        self.assertAlmostEqual(expert[2]['cost_target_usd'],.184)

    async def test_overshoot_is_fully_charged_and_prevents_another_inference(self):
        report,events=await self.scenario(.25,'interrupted')
        self.assertEqual(report['terminal_reason'],'budget_exhausted_after_integration')
        self.assertAlmostEqual(report['cost_usd'],.271)
        self.assertEqual(sum(x[0]=='gate' for x in events),1)

    async def test_unknown_usage_cannot_qualify_or_trigger_more_calls(self):
        report,events=await self.scenario(None,'error')
        self.assertFalse(report['usage_complete']);self.assertIsNone(report['cost_usd'])
        self.assertEqual(sum(x[0]=='gate' for x in events),1)

    def test_two_native_interventions_maximum(self):
        self.assertEqual(Budget(1,advisor_calls=2).advisor_allowance(),0)

class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_command_is_exact_data_and_limit_precedes_execution(self):
        observed=[]
        async def remote(command):
            observed.append(command);return SimpleNamespace(return_code=7,stdout='actual output',stderr='actual error')
        relay=CommandRelay(remote,1)
        command='printf "$(must-not-run-on-host)"\n# \'quote'
        result=await relay.handle({'command':command})
        self.assertEqual(observed,[command]);self.assertEqual(result['exit_code'],7)
        self.assertIn('controller_error',await relay.handle({'command':'second'}))
        self.assertEqual(observed,[command])

    async def test_transport_error_is_not_a_fake_command_failure(self):
        async def remote(command):raise ConnectionError('backend missing')
        with self.assertRaises(ConnectionError):await CommandRelay(remote).handle({'command':'true'})

if __name__=='__main__':unittest.main()
