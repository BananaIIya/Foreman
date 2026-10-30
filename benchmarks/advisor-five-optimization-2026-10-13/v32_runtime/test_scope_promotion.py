import unittest
from .coordinator import Coordinator, Budget
from v4_runtime.policy import route as old_route

class ScopePromotionTests(unittest.IsolatedAsyncioTestCase):
    async def scenario(self,initial_cost=.024692, first_action='prepare',direct_consult=False):
        work=[];calls=[];emitted={};gates=[]
        async def planner(*args):return {'status':'completed','usage':{'cost_usd':.0012165},'work_order':{'objective':'Reusable component'}}
        async def worker(phase,payload,policy,cap):
            work.append((phase,payload,cap));return {'status':'preparation_observed','usage':{'cost_usd':initial_cost if len(work)==1 else .006},'evidence':{'text':'Actual component failure' if len(work)==1 else 'Component repaired; whole-task work remains'}}
        base={'action':first_action,'issue':'Routine component check failed','evidence':'Actual dependency exception','paths':['/app/helper.py'],'intervention':'Worker: repair ONLY this helper and do not implement the final assignment.','acceptance':'Synthetic helper check passes'}
        async def decision(phase,payload):
            gates.append(payload)
            action=first_action if len(gates)==1 else 'deliver' if calls else 'advisor'
            d=dict(base,action=action)
            if action=='advisor':d.update(intervention='Complete the original assignment using valid helper',acceptance='Full task')
            return {'status':'completed','usage':{'cost_usd':.001},'decision':d}
        async def advisor(phase,payload):
            calls.append(payload);return {'status':'completed','usage':{'cost_usd':.015},'evidence':{'text':'{"status":"completed"}'}}
        async def finalizer(*args):return None
        c=Coordinator(.1877124,'cursor/grok-4.7-low',worker,decision,advisor,lambda k,v:emitted.update({k:v}),planner,finalizer)
        if direct_consult:
            result=await c.consult('Complete original assignment',base,{'text':'Actual error'},0)
        else:result=await c.run('Complete original assignment')
        return result,work,calls,emitted

    async def test_actual_legacy_reserve_bug_no_longer_promotes_affordable_component_repair(self):
        b=Budget(.1877124,spent=.0012165+.024692+.001)
        self.assertEqual(b.preparation_allowance('cursor/grok-4.7-low'),0)
        self.assertGreater(.1877124*.20-.024692,.005)
        self.assertEqual(old_route({'action':'prepare','issue':'component failure','evidence':'actual exception','paths':['/app/helper.py']},'discovery',b,[],'cursor/grok-4.7-low'),'advisor')
        r,w,c,e=await self.scenario()
        self.assertEqual(len(w),2)
        self.assertAlmostEqual(w[1][2],.01285048)
        self.assertEqual(e['decision-0']['action'],'continue')
        self.assertEqual(len(c),1)
        self.assertEqual(r['terminal_reason'],'deliver')

    async def test_exhausted_unit_budget_transfers_full_ownership_not_worker_exclusions(self):
        r,w,c,e=await self.scenario(initial_cost=.036)
        self.assertEqual(len(w),1);self.assertEqual(len(c),1)
        self.assertNotIn('do not implement',c[0]['contract']['intervention'])
        self.assertIn('original required artifacts',c[0]['contract']['intervention'])
        receipt=e['advisor-scope-promotion-0']
        self.assertIn('do not implement',receipt['original_worker_decision']['intervention'])
        self.assertTrue(receipt['no_new_model_call']);self.assertFalse(receipt['verifier_feedback'])
        self.assertEqual(c[0]['original_task'],'Complete original assignment')

    async def test_component_false_delivery_promotes_instead_of_repeating_component(self):
        r,w,c,e=await self.scenario(first_action='deliver')
        self.assertEqual(len(w),1);self.assertEqual(len(c),1)
        self.assertEqual(e['advisor-scope-promotion-0']['reason'],'component_is_not_original_deliverable')
        self.assertIn('not the deliverable',c[0]['contract']['acceptance'])

    async def test_fallback_consult_cannot_forward_obsolete_worker_scope(self):
        r,w,c,e=await self.scenario(direct_consult=True)
        self.assertFalse(w);self.assertEqual(len(c),1)
        self.assertNotIn('Worker: repair ONLY',c[0]['contract']['intervention'])
        self.assertEqual(e['advisor-scope-promotion-0']['reason'],'controller_promoted_worker_action')

if __name__=='__main__':unittest.main()
