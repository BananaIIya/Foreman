import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from .native import AdvisorSession
from .live_review import feedback, allowance
from . import test_flow as flow
phase = flow.phase


class FakeSession(AdvisorSession):
    def __init__(self, root, reviewer, costs, delay=.004):
        super().__init__(root,root,'gpt-6.1-sol','high')
        self.live_reviewer=reviewer;self.costs=costs;self.delay=delay
        self.emitted=[];self.calls=[];self.producer=None;self.current_turn='turn'
    async def rpc(self,method,params):
        self.calls.append((method,params))
        if method=='thread/start':return {'model':self.model,'reasoningEffort':self.effort,'thread':{'id':'thread'}}
        if method=='turn/start':
            async def produce():
                for cost in self.costs:
                    await asyncio.sleep(self.delay)
                    event={'method':'thread/tokenUsage/updated','params':{'turnId':'turn','test_cost':cost}}
                    self.emitted.append(event);await self.notifications.put(event)
                    event={'method':'item/completed','params':{'item':{'type':'commandExecution','id':str(cost),'command':'actual computation','exitCode':0,'aggregatedOutput':'observed result'}}}
                    self.emitted.append(event);await self.notifications.put(event)
                    # Private reasoning must never enter reviewer evidence.
                    event={'method':'item/completed','params':{'item':{'type':'reasoning','text':'PRIVATE_SENTINEL'}}}
                    self.emitted.append(event);await self.notifications.put(event)
                await asyncio.sleep(self.delay)
                event={'method':'turn/completed','params':{'turn':{'id':'turn','status':'completed'}}}
                self.emitted.append(event);await self.notifications.put(event)
            self.producer=asyncio.create_task(produce())
            return {'turn':{'id':'turn'}}
        return {}
    async def budget_notice(self,*args):pass


def review(cost=.003, action='check'):
    return {'phase':'decision-live-advisor-0','status':'completed','usage':{'cost_usd':cost},
            'evidence':{'text':'public review'},'review':{'action':action,'requirement':'Original requirement',
             'observed_evidence':'Observed output contradicts current assumption','proposed_check':'Recheck original input','reason':'Avoid unsupported inference'}}


class LiveReviewTests(unittest.IsolatedAsyncioTestCase):
    async def run_fake(self, callback, costs, delay=.004):
        with tempfile.TemporaryDirectory() as tmp:
            client=FakeSession(Path(tmp),callback,costs,delay)
            def account(events, model, turn):
                values=[e['params']['test_cost'] for e in events if e.get('method')=='thread/tokenUsage/updated']
                return {'cost_usd':values[-1] if values else None}
            with patch('v49_runtime.native.account_codex',side_effect=account),patch('v49_runtime.native.read_events',side_effect=lambda _:client.emitted):
                result=await client.turn('remote','original task','policy','/app',budget_usd=1.)
            await client.producer
            receipt=json.loads((Path(tmp)/'live-review-receipt.json').read_text())
            return client,result,receipt

    async def test_one_review_and_same_turn_steer_with_public_evidence_only(self):
        callback=AsyncMock(return_value=review())
        c,r,receipt=await self.run_fake(callback,[.2,.51,.6,.7])
        self.assertEqual(callback.await_count,1)
        self.assertNotIn('PRIVATE_SENTINEL',str(callback.await_args))
        steer=[args for method,args in c.calls if method=='turn/steer']
        self.assertEqual(len(steer),1);self.assertEqual(steer[0]['threadId'],'thread');self.assertEqual(steer[0]['expectedTurnId'],'turn')
        self.assertEqual(receipt['review_cost_usd'],.003)
        self.assertEqual(r['usage']['cost_usd'],.7) # Advisor usage excludes reviewer; ledger adds it.
        self.assertEqual(r['live_review']['usage']['cost_usd'],.003)

    async def test_no_concern_does_not_steer_or_retry(self):
        callback=AsyncMock(return_value=review(action='continue'))
        c,r,receipt=await self.run_fake(callback,[.51,.6,.7])
        self.assertFalse(any(method=='turn/steer' for method,_ in c.calls))
        self.assertEqual(callback.await_count,1)
        self.assertEqual(receipt['feedback_status'],'no_grounded_change_requested')

    async def test_slow_review_is_charged_after_expert_completes_without_restart(self):
        async def callback(*args):await asyncio.sleep(.04);return review()
        c,r,receipt=await self.run_fake(callback,[.51],.001)
        self.assertEqual(receipt['feedback_status'],'turn_already_terminal')
        self.assertEqual(r['live_review']['usage']['cost_usd'],.003)
        self.assertEqual(sum(method=='turn/start' for method,_ in c.calls),1)
        self.assertFalse(any(method=='turn/steer' for method,_ in c.calls))

    async def test_slow_review_does_not_block_native_budget_interrupt(self):
        async def callback(*args):await asyncio.sleep(.04);return review()
        c,r,receipt=await self.run_fake(callback,[.51,1.01],.001)
        self.assertEqual(r['interrupted'],'budget_boundary_exceeded')
        self.assertTrue(any(method=='turn/interrupt' for method,_ in c.calls))
        self.assertEqual(r['live_review']['usage']['cost_usd'],.003)

    async def test_actual_review_overspend_reduces_expert_allowance(self):
        callback=AsyncMock(return_value=review(cost=.4))
        c,r,receipt=await self.run_fake(callback,[.51,.7])
        self.assertEqual(r['interrupted'],'budget_boundary_exceeded')
        self.assertEqual(receipt['review_cost_usd'],.4)

    async def test_unknown_review_usage_stops_without_zero_cost_claim(self):
        callback=AsyncMock(return_value=review(cost=None))
        c,r,receipt=await self.run_fake(callback,[.51,.6])
        self.assertEqual(r['interrupted'],'unknown_review_usage')
        self.assertIsNone(receipt['review_cost_usd'])

    async def test_early_completion_does_not_start_a_review(self):
        callback=AsyncMock(return_value=review())
        c,r,receipt=await self.run_fake(callback,[.2,.3])
        self.assertEqual(callback.await_count,0);self.assertIsNone(r['live_review'])
        self.assertEqual(receipt['attempts'],0)

    def test_unsupported_review_is_not_a_command(self):
        self.assertIsNone(feedback({'action':'check','requirement':'x','observed_evidence':'','proposed_check':'x'}))
        self.assertLessEqual(allowance(1),.005)


class LedgerTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_live_review_fee_prevents_savings_claim(self):
        c=flow.FlowTests().setup_flow(['advisor'],[phase(.3,'{"status":"completed"}',live_review=review(cost=None))])
        r=await c.run('Original requirements')
        self.assertFalse(r['usage_complete']);self.assertIsNone(r['cost_usd'])
        self.assertEqual(r['terminal_reason'],'budget_exhausted')

    async def test_live_review_cost_is_recorded_once_in_whole_trial(self):
        c=flow.FlowTests().setup_flow(['advisor','deliver'],[phase(.3,'{"status":"completed"}',live_review=review())])
        r=await c.run('Original requirements')
        self.assertAlmostEqual(r['cost_usd'],.315)
        reviews=[x for x in r['phases'] if x['phase']=='decision-live-advisor-0']
        self.assertEqual(len(reviews),1);self.assertEqual(reviews[0]['cost_usd'],.003)


if __name__=='__main__':unittest.main()
