import unittest
from v4_runtime.policy import Budget,route
from v4_runtime.coordinator import Coordinator

def decision(action='prepare'):
 return {'action':action,'issue':'specialist interpretation','evidence':'original input exists','paths':['/app/input.png'],'intervention':'inspect','acceptance':'check'}
class Tests(unittest.TestCase):
 def test_preparation_cannot_spend_reserved_expert_budget(self):
  b=Budget(.2,spent=.03,worker_phase_floor=.025)
  self.assertEqual(b.preparation_allowance('cursor/grok-4.7-low'),0)
  self.assertEqual(route(decision(),'discovery',b,[],'cursor/grok-4.7-low'),'advisor')
  self.assertGreater(b.advisor_allowance(),0)
 def test_affordable_routine_preparation_remains_available(self):
  b=Budget(1.5,spent=.01,worker_phase_floor=.002)
  allowance=b.preparation_allowance('gpt-6-luna')
  self.assertGreater(allowance,0)
  self.assertGreaterEqual(b.ceiling-b.spent-allowance,b.baseline*.45+b.reserve('gpt-6-luna'))
  self.assertEqual(route(decision(),'discovery',b,[]),'prepare')
class FlowTests(unittest.IsolatedAsyncioTestCase):
 async def test_budget_guard_routes_to_expert_instead_of_ending_before_expert(self):
  phases=[];gates=iter([decision(),decision('deliver')])
  def result(cost):return {'usage':{'cost_usd':cost},'status':'completed','evidence':{'text':'observed'}}
  async def worker(phase,payload,policy,cap):phases.append(phase);return result(.025 if 'discovery' in phase else .015)
  async def gate(phase,payload):return dict(result(.002),decision=next(gates))
  async def advisor(phase,payload):
   self.assertEqual(payload['contract']['action'],'advisor')
   self.assertEqual(payload['contract']['deferred_worker_action'],'prepare')
   self.assertNotEqual(payload['contract']['intervention'],'inspect')
   phases.append(phase);return dict(result(.05),patch={'status':'patch','files':[{'path':'/app/output.py','content':'x=1\n'}],'validation_commands':[],'requested_evidence':[],'handoff':'run'})
  async def apply(patch):return {'applied':True,'paths':['/app/output.py']}
  c=Coordinator(.2,'cursor/grok-4.7-low',worker,gate,advisor,apply,lambda *args:None)
  r=await c.run('Original task')
  self.assertEqual(r['advisor_calls'],1);self.assertEqual(r['integrations'],1);self.assertEqual(r['preparations'],0)
  self.assertEqual(r['terminal_reason'],'deliver');self.assertFalse(any('preparation' in p for p in phases))
if __name__=='__main__':unittest.main()
