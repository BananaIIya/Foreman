import unittest
from .completion import unfinished_reason
from .coordinator import Coordinator


class CompletionTests(unittest.IsolatedAsyncioTestCase):
    def test_budget_checkpoint_is_not_completion_but_explicit_completion_is_eligible(self):
        r={'interrupted':'budget_boundary_exceeded','evidence':{'text':'Candidate exists; still checking uncertain assumptions'}}
        self.assertEqual(unfinished_reason(r),'native_checkpoint_without_completion_claim')
        r['evidence']['text']='Checked original requirements. {"status":"completed"}'
        self.assertIsNone(unfinished_reason(r))
        r['evidence']['text']='{"status":"needs_advisor","unresolved_assumptions":["original requirement uncertain"]}'
        self.assertEqual(unfinished_reason(r),'expert_explicitly_reported_unfinished_work')

    async def test_false_delivery_routes_to_same_advisor_callback_without_grader(self):
        calls=[];receipts={}
        async def planner(*args):return {'status':'completed','usage':{'cost_usd':.001},'work_order':{}}
        async def worker(*args):return {'status':'completed','usage':{'cost_usd':.02},'evidence':{'text':'Original inputs prepared'}}
        async def decision(*args):
            return {'status':'completed','usage':{'cost_usd':.001},'decision':{'action':'advisor' if not calls else 'deliver',
                'issue':'Uncertain original requirement','evidence':'Observed progress','paths':['/app/output'],
                'intervention':'Complete original task','acceptance':'Original checks'}}
        async def advisor(phase,payload):
            calls.append((phase,payload))
            return {'status':'interrupted' if len(calls)==1 else 'completed','usage':{'cost_usd':.03},
                    'interrupted':'budget_boundary_exceeded' if len(calls)==1 else None,
                    'evidence':{'text':'Still checking assumptions' if len(calls)==1 else '{"status":"completed"}'}}
        async def finalizer(*args):return None
        c=Coordinator(.5,'cursor/grok-4.7-low',worker,decision,advisor,lambda k,v:receipts.update({k:v}),planner,finalizer)
        r=await c.run('Original task')
        self.assertEqual(len(calls),2)
        self.assertEqual(r['terminal_reason'],'deliver')
        self.assertAlmostEqual(r['cost_usd'],.083)  # V58 has no planner phase
        self.assertEqual(receipts['completion-review-decision-1']['original_decision']['action'],'deliver')
        self.assertFalse(receipts['completion-review-decision-1']['verifier_feedback'])
        self.assertNotIn('completion-review-decision-2',receipts)


if __name__=='__main__':unittest.main()
