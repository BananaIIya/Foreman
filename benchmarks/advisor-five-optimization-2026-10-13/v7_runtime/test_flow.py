import json
import unittest
from types import SimpleNamespace
from v7_runtime.coordinator import Coordinator
from v7_runtime.execution import execute_commands
from v7_runtime.policy import validate_patch
from v7_runtime.policy import Budget

def result(cost=.001, text='actual observations'):
    return {'usage': {'cost_usd': cost}, 'status': 'completed', 'evidence': {'text': text}}

def gate(action):
    return {'action': action, 'issue': 'uncertain algorithm', 'evidence': 'original inputs inspected',
            'paths': ['/app/input'], 'intervention': 'resolve uncertainty', 'acceptance': 'original requirements'}

def patch(status='patch'):
    return {'status': status, 'files': [{'path': '/app/program.py', 'content': 'print(42)\n'}],
            'setup_commands': ['prepare-dependency'], 'validation_commands': ['run-program'],
            'requested_evidence': [], 'handoff': 'check actual output'}

class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def simulate(self, patches, actions=('advisor', 'deliver'), costs=None, failing=False, baseline=1):
        events=[]; steps=iter(actions); answers=iter(patches); prices=iter(costs or [.05]*len(patches))
        async def worker(phase,payload,policy,cap):
            events.append(('worker',phase,payload));return result(.02)
        async def decision(phase,payload):
            events.append(('gate',phase,payload));return dict(result(),decision=gate(next(steps)))
        async def advisor(phase,payload):
            events.append(('advisor',phase,payload));return dict(result(next(prices)),patch=next(answers))
        async def apply(p):
            events.append(('apply',p));return {'applied': True,'paths':[x['path'] for x in p['files']]}
        async def execute(phase,p):
            async def run(command):
                events.append(('command',command));return SimpleNamespace(return_code=1 if failing else 0,stdout='observed 42',stderr='actual failure' if failing else '')
            return await execute_commands(p,run)
        c=Coordinator(baseline,'cursor/grok-4.7-low',worker,decision,advisor,apply,execute,lambda *args:None)
        return await c.run('Fresh original task'),events

    async def test_complete_patch_executes_before_delivery_without_worker_inference(self):
        r,e=await self.simulate([patch()])
        self.assertEqual(r['terminal_reason'],'deliver')
        self.assertEqual(r['integrations'],0);self.assertEqual(r['execution_only_integrations'],1)
        self.assertEqual([x[1] for x in e if x[0]=='command'],['prepare-dependency','run-program'])
        self.assertEqual(sum(x[0]=='worker' for x in e),1)
        self.assertAlmostEqual(r['cost_usd'],.072)
        delivered=json.loads([x for x in e if x[0]=='gate'][-1][2]['worker_evidence']['text'])
        self.assertTrue(delivered['all_commands_succeeded'])
        self.assertEqual(delivered['checks'][1]['stdout'],'observed 42')

    async def test_one_diagnostic_return_goes_directly_to_expert(self):
        r,e=await self.simulate([patch('needs_context'),patch()])
        self.assertEqual(r['diagnostic_returns'],1);self.assertEqual(r['advisor_calls'],2)
        self.assertEqual(r['execution_only_integrations'],2)
        self.assertEqual(sum(x[0]=='worker' for x in e),1)
        self.assertEqual(sum(x[0]=='gate' for x in e),2)
        expert=[x for x in e if x[0]=='advisor'][1]
        self.assertIn('observed 42',expert[2]['worker_evidence']['text'])
        self.assertAlmostEqual(r['cost_usd'],.122)

    async def test_unused_worker_reserve_does_not_block_affordable_diagnostic_return(self):
        r,e=await self.simulate([patch('needs_context'),patch()],costs=[.169,.025],baseline=.33)
        self.assertEqual(r['advisor_calls'],2)
        self.assertEqual(r['terminal_reason'],'deliver')
        self.assertAlmostEqual(r['cost_usd'],.216)
        self.assertLess(r['cost_usd'],r['target_ceiling_usd'])

    async def test_diagnostic_success_is_not_delivery(self):
        r,e=await self.simulate([patch('needs_context'),patch('needs_context')],('advisor','deliver','deliver'))
        self.assertEqual(r['advisor_calls'],2)
        self.assertEqual(r['diagnostic_returns'],1)
        self.assertEqual([x[1] for x in e if x[0]=='worker'],['worker-discovery','worker-implementation-1'])

    async def test_invalid_handoff_gets_real_fallback(self):
        for bad in (None, {'status':'patch'}, dict(patch(),files=[{'path':'/app/../secret','content':'x'}])):
            r,e=await self.simulate([bad,bad])
            self.assertEqual(r['integrations'],1)
            self.assertEqual(r['advisor_calls'],2)
            self.assertFalse(any(x[0] in ('command','apply') for x in e))

    async def test_prose_only_patch_is_repaired_by_expert_and_charged(self):
        prose=dict(patch(),files=[],validation_commands=[],setup_commands=[],handoff='Only a prose conclusion')
        r,e=await self.simulate([prose,patch()])
        self.assertEqual(r['advisor_calls'],2);self.assertEqual(r['integrations'],0)
        self.assertEqual(r['execution_only_integrations'],1)
        self.assertAlmostEqual(r['cost_usd'],.122)
        calls=[x for x in e if x[0]=='advisor']
        self.assertIn('complete executable replacement files',calls[0][2]['contract']['intervention'])
        self.assertIn('failed the executable protocol',calls[1][2]['worker_evidence']['text'])

    async def test_unknown_usage_executes_but_cannot_qualify(self):
        r,e=await self.simulate([patch()],costs=[None])
        self.assertIsNone(r['cost_usd']);self.assertFalse(r['usage_complete'])
        self.assertEqual(r['execution_only_integrations'],1)
        self.assertAlmostEqual(r['known_cost_lower_bound_usd'],.021)

    async def test_overspend_executes_but_stops_model_calls(self):
        r,e=await self.simulate([patch()],costs=[1.2])
        self.assertEqual(r['terminal_reason'],'budget_exhausted_after_integration')
        self.assertAlmostEqual(r['cost_usd'],1.221)
        self.assertEqual(sum(x[0]=='gate' for x in e),1)
        self.assertEqual(r['execution_only_integrations'],1)

    async def test_failed_diagnostic_returns_actual_error(self):
        r,e=await self.simulate([patch('needs_context'),patch()],actions=('advisor','deliver','deliver'),failing=True)
        expert=[x for x in e if x[0]=='advisor'][1]
        obs=json.loads(expert[2]['worker_evidence']['text'])
        self.assertFalse(obs['all_commands_succeeded'])
        self.assertEqual(obs['checks'][0]['stderr'],'actual failure')
        self.assertEqual(len(obs['unexecuted_commands']),1)
        self.assertTrue(any(x[:2]==('worker','worker-implementation-1') for x in e))

class ExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_failure_propagates(self):
        async def broken(command):raise ConnectionError('remote backend unavailable')
        with self.assertRaises(ConnectionError):await execute_commands(patch(),broken)

    async def test_stops_on_first_nonzero_and_marks_skipped(self):
        calls=[]
        async def fail(command):
            calls.append(command);return SimpleNamespace(return_code=7,stdout='partial',stderr='error')
        r=await execute_commands(patch(),fail)
        self.assertEqual(calls,['prepare-dependency'])
        self.assertEqual(r['execution']['checks'][0]['return_code'],7)
        self.assertFalse(r['execution']['all_commands_succeeded'])
        self.assertEqual(r['execution']['unexecuted_commands'],[{'kind':'validation','command':'run-program'}])

class ProtocolTests(unittest.TestCase):
    def test_advisor_reserve_matches_execution_only_architecture(self):
        budget=Budget(.33,spent=.19,advisor_calls=1)
        self.assertAlmostEqual(budget.advisor_allowance(),.036)
        budget.advisor_calls=2
        self.assertEqual(budget.advisor_allowance(),0)
    def test_diagnostic_files_use_same_guards(self):
        validate_patch(patch('needs_context'))
        for path in ['/tmp/x','/app/../x','/app/.agents/x','relative']:
            p=patch('needs_context');p['files'][0]['path']=path
            with self.assertRaises(ValueError):validate_patch(p)
        p=patch();p['files'][0]['content']='x'*400001
        with self.assertRaises(ValueError):validate_patch(p)

    def test_blocked_response_cannot_run_commands(self):
        p=patch('blocked');p['files']=[]
        with self.assertRaises(ValueError):validate_patch(p)

    def test_no_empty_or_excessive_command_handoff(self):
        for commands in ([],[' '],['true']*9):
            p=patch();p['validation_commands']=commands
            with self.assertRaises(ValueError):validate_patch(p)

if __name__=='__main__':unittest.main()
