import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from v11_runtime.test_worker import answer
from .worker import bounded_worker
from .coordinator import Coordinator
from .policy import INITIAL_WORK, preparation_images

class WorkerDraftTests(unittest.IsolatedAsyncioTestCase):
    async def simulate(self, replies, cap=.2):
        replies=iter(replies); prompts=[]; executed=[]
        async def native(folder,prompt,effort):
            prompts.append(prompt);return next(replies)
        async def execute(command):
            executed.append(command)
            return SimpleNamespace(return_code=7 if command=='fails' else 0, stdout='actual derived output', stderr='specific failure' if command=='fails' else '')
        with tempfile.TemporaryDirectory() as tmp:
            r=await bounded_worker(Path(tmp)/'worker-discovery','Original full task','low',cap,execute,native)
        return r,prompts,executed

    async def test_implements_observes_then_repairs_and_forwards_real_final_results(self):
        r,p,c=await self.simulate([answer(commands=['implement','fails','must-not-run']),answer(commands=['repair','check'])])
        self.assertEqual(c,['implement','fails','repair','check'])
        self.assertEqual(len(p),2)
        self.assertIn('specific failure',p[1])
        self.assertEqual(r['status'],'draft_observed')
        self.assertAlmostEqual(r['usage']['cost_usd'],.02)
        e=json.loads(r['evidence']['text'])
        self.assertEqual(e['protocol_history'][0]['unexecuted_commands'],['must-not-run'])
        self.assertEqual(e['actual_command_results'][-1]['stdout'],'actual derived output')
        self.assertTrue(e['report_is_before_execution'])

    async def test_self_request_after_observation_stops_without_extra_call(self):
        r,p,c=await self.simulate([answer(commands=['derive']),answer('needs_advisor')])
        self.assertEqual(r['status'],'needs_advisor')
        self.assertEqual(len(p),2)
        self.assertEqual(c,['derive'])
        self.assertIn('actual derived output',p[1])

    async def test_cost_boundary_and_unknown_usage_preserved(self):
        for cost in [.03,None]:
            r,p,c=await self.simulate([answer(commands=['derive'],cost=cost)],cap=.02)
            self.assertEqual(len(p),1)
            self.assertEqual(r['usage']['cost_usd'],cost)
            self.assertEqual(r['status'],'controller_cost_boundary')

    async def test_cannot_execute_a_fifth_command(self):
        r,p,c=await self.simulate([answer(commands=['one','two','three']),answer(commands=['four','five'])])
        self.assertEqual(c,['one','two','three'])
        self.assertEqual(r['status'],'controller_command_limit')
        self.assertAlmostEqual(r['usage']['cost_usd'],.02)

    def test_original_images_available_to_initial_and_repair_worker(self):
        images=[{'data':'synthetic fixture','mimeType':'image/png'}]
        self.assertEqual(preparation_images('worker-discovery',images),images)
        self.assertEqual(preparation_images('worker-preparation-0',images),images)

class DraftRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def scenario(self, actions, worker_cost=.02):
        work=[];advice=[];gates=[];receipts={}
        async def planner(*args):return {'status':'completed','usage':{'cost_usd':.001},'work_order':{'work_kind':'bounded_implementation','objective':'Create real artifact'}}
        async def worker(phase,payload,policy,cap):
            work.append((phase,payload,policy,cap))
            return {'status':'draft_observed','usage':{'cost_usd':worker_cost},'evidence':{'text':'Observed candidate and an exact failed requirement'}}
        async def decision(phase,payload):
            gates.append(payload)
            return {'status':'completed','usage':{'cost_usd':.001},'decision':{'action':actions[len(gates)-1], 'issue':'Observed issue '+str(len(gates)), 'evidence':payload['worker_evidence']['text'],'paths':['/app/out'],'intervention':'Repair actual requirement','acceptance':'Original substantive check'}}
        async def advisor(phase,payload):
            advice.append(payload)
            return {'status':'completed','usage':{'cost_usd':.02},'evidence':{'text':'{"status":"completed"}'}}
        async def finalizer(*args):return None
        c=Coordinator(.5,'cursor/grok-4.7-low',worker,decision,advisor,lambda k,v:receipts.update({k:v}),planner,finalizer)
        r=await c.run('Full original task, including all acceptance requirements')
        return r,work,advice,gates

    async def test_supported_worker_candidate_can_deliver_without_advisor(self):
        r,w,a,g=await self.scenario(['deliver'])
        self.assertEqual(r['terminal_reason'],'deliver')
        self.assertEqual(len(a),0)
        self.assertIn('all acceptance requirements',w[0][1]['original_task'])
        self.assertEqual(w[0][2],INITIAL_WORK)
        self.assertEqual(g[0]['stage'],'delivery')
        self.assertAlmostEqual(r['cost_usd'],.022)

    async def test_routine_repair_shares_draft_allowance_then_escalates(self):
        r,w,a,g=await self.scenario(['continue','continue','deliver'])
        self.assertEqual(len(w),2)
        self.assertEqual(len(a),1)
        self.assertAlmostEqual(w[0][3],.1)
        self.assertAlmostEqual(w[1][3],.08)
        self.assertIn('actual_previous_execution',w[1][1])
        self.assertIn('all acceptance requirements',w[1][1]['original_task'])
        self.assertEqual(a[0]['worker_evidence']['text'],g[1]['worker_evidence']['text'])
        self.assertEqual(r['terminal_reason'],'deliver')
        self.assertFalse(r['verifier_feedback'])

    async def test_spent_worker_allowance_cannot_open_extra_repair(self):
        r,w,a,g=await self.scenario(['continue','deliver'],worker_cost=.105)
        self.assertEqual(len(w),1)
        self.assertEqual(len(a),1)
        self.assertAlmostEqual(r['cost_usd'],.128)

if __name__=='__main__':unittest.main()
