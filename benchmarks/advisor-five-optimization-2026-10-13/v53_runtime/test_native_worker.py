import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .native import AdvisorSession
from .test_delegation import WORK
from .test_flow import FlowTests, phase
from .worker_batch import execute_batch
from types import SimpleNamespace


class FakeSession(AdvisorSession):
    def __init__(self,path,worker,mid_cost=None,first_usage=True):
        super().__init__(path,path,'gpt-6.1-sol','high')
        self.routine_worker=worker;self.routine_worker_limit=.4;self.routine_worker_reserve=.1
        self.emitted=[];self.calls=[];self.mid_cost=mid_cost;self.answers=[];self.first_usage=first_usage
    async def push(self,event):
        self.emitted.append(event);await self.notifications.put(event)
    async def rpc(self,method,params):
        self.calls.append((method,params))
        if method=='thread/start':return {'model':self.model,'reasoningEffort':self.effort,'thread':{'id':'thread'}}
        if method=='turn/start':
            async def produce():
                await asyncio.sleep(.002)
                if self.first_usage:
                    await self.push({'method':'thread/tokenUsage/updated','params':{'turnId':'turn','test_cost':.2}})
                await asyncio.sleep(.002)
                args={'threadId':'thread','turnId':'turn','callId':'call','tool':'routine_worker','arguments':WORK}
                self.answers.append(await self.client_tool_handler(dict(args,turnId='wrong')))
                running=asyncio.create_task(self.client_tool_handler(args))
                await asyncio.sleep(.005)
                if self.mid_cost is not None:
                    await self.push({'method':'thread/tokenUsage/updated','params':{'turnId':'turn','test_cost':self.mid_cost}})
                    await asyncio.sleep(.002)
                self.answers.append(await running)
                if not self.first_usage:
                    await self.push({'method':'thread/tokenUsage/updated','params':{'turnId':'turn','test_cost':.2}})
                await self.push({'method':'turn/completed','params':{'turn':{'id':'turn','status':'completed'}}})
            self.producer=asyncio.create_task(produce());return {'turn':{'id':'turn'}}
        return {}
    async def budget_notice(self,*args):pass


class NativeWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def simulate(self,cost,mid_cost=None,first_usage=True):
        async def worker(work,cap):
            await asyncio.sleep(.02)
            return {'phase':'worker-in-turn-advisor-0','model_calls':1,'status':'completed',
                    'usage':{'cost_usd':cost},'evidence':{'commands':[{'exit_code':1,'stdout':'failure retained'}]}}
        with tempfile.TemporaryDirectory() as tmp:
            c=FakeSession(Path(tmp),worker,mid_cost,first_usage)
            def account(events,*args):
                values=[e['params']['test_cost'] for e in events if e.get('method')=='thread/tokenUsage/updated']
                return {'cost_usd':values[-1] if values else None}
            with patch('v53_runtime.native.account_codex',side_effect=account),patch('v53_runtime.native.read_events',side_effect=lambda _:c.emitted):
                result=await c.turn('remote','original','policy','/app',budget_usd=1.)
            await c.producer
            receipt=json.loads((Path(tmp)/'in-turn-worker-receipt.json').read_text())
        return c,result,receipt

    async def test_session_binding_same_turn_and_separate_native_fee(self):
        c,r,receipt=await self.simulate(.1)
        self.assertEqual(c.answers[0]['status'],'wrong_native_session')
        self.assertEqual(len(receipt['records']),1)
        self.assertEqual((receipt['same_thread'],receipt['same_turn']),('thread','turn'))
        self.assertEqual(r['usage']['cost_usd'],.2)
        self.assertEqual(r['in_turn_workers'][0]['usage']['cost_usd'],.1)
        self.assertEqual(sum(m=='turn/start' for m,_ in c.calls),1)

    async def test_unknown_fee_interrupts_before_continuation(self):
        c,r,receipt=await self.simulate(None)
        self.assertEqual(r['interrupted'],'unknown_delegated_worker_usage')
        self.assertFalse(receipt['usage_complete']);self.assertIsNone(receipt['cost_usd'])

    async def test_tool_before_first_usage_is_explicit_boundary_estimate(self):
        c,r,receipt=await self.simulate(.1,first_usage=False)
        self.assertEqual(len(receipt['records']),1)
        self.assertIsNone(receipt['budget_observations'][0]['native_cost_usd'])
        self.assertTrue(receipt['budget_observations'][0]['inflight_response_cost_may_be_pending'])
        self.assertEqual(r['usage']['cost_usd'],.2)
        self.assertEqual(r['in_turn_workers'][0]['usage']['cost_usd'],.1)

    async def test_pending_reservation_does_not_block_native_budget_monitor(self):
        c,r,receipt=await self.simulate(.1,mid_cost=.7)
        self.assertEqual(r['interrupted'],'budget_boundary_exceeded')
        self.assertEqual(receipt['cost_usd'],.1)

    async def test_worker_overshoot_is_charged_and_interrupts(self):
        c,r,receipt=await self.simulate(.85)
        self.assertEqual(r['interrupted'],'combined_worker_budget_exceeded')
        self.assertEqual(receipt['cost_usd'],.85)

    async def test_coordinator_charges_worker_once_and_halts_on_unknown(self):
        for fee in [.1,None]:
            worker=phase(fee);worker.update(phase='worker-in-turn-advisor-0',model_calls=1)
            c=FlowTests().setup_flow(['advisor','deliver'],[phase(.3,'{"status":"completed"}',in_turn_workers=[worker])])
            r=await c.run('Original requirements')
            self.assertEqual(len([p for p in r['phases'] if p['phase']==worker['phase']]),1)
            if fee is None:self.assertIsNone(r['cost_usd'])
            else:self.assertAlmostEqual(r['cost_usd'],.412)

    async def test_luna_batch_preserves_usage_and_skips_after_actual_failure(self):
        reply=phase(.01,json.dumps({'status':'commands','commands':['first','second'],'report':'Intended checks'}))
        calls=[]
        async def execute(command):
            calls.append(command);return SimpleNamespace(return_code=1,stdout='',stderr='actual error')
        r=await execute_batch(reply,execute)
        self.assertEqual(calls,['first']);self.assertEqual(r['usage']['cost_usd'],.01)
        self.assertEqual(json.loads(r['evidence']['text'])['unexecuted_commands'],['second'])


if __name__=='__main__':unittest.main()
