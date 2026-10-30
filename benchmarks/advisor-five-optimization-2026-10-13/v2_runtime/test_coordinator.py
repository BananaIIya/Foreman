import unittest
from v2_runtime.coordinator import Coordinator
from v2_runtime.policy import validate_patch

def result(cost=.01,text='evidence'):
 return {'usage':{'cost_usd':cost},'status':'completed','evidence':{'text':text}}
def gate(action):
 return {'action':action,'issue':'risk','evidence':'observed','paths':['/app/f.py'],'intervention':'fix','acceptance':'check'}
def patch():
 return {'status':'patch','files':[{'path':'/app/f.py','content':'value = 1\n'}],'validation_commands':['python /app/f.py'],'requested_evidence':[],'handoff':'Run checks; not executed yet.'}
class Tests(unittest.IsolatedAsyncioTestCase):
 async def simulate(self,actions,advice,advisor_cost=.1):
  events=[];actions=iter(actions)
  async def worker(phase,payload,policy,cap):
   events.append(('worker',phase,payload,cap));return result()
  async def decision(phase,payload):
   events.append(('gate',phase));return dict(result(),decision=gate(next(actions)))
  async def advisor(phase,payload):return dict(result(advisor_cost),patch=advice)
  async def apply(p):events.append(('apply',p));return {'applied':True,'paths':[f['path'] for f in p['files']]}
  c=Coordinator(1,'gpt-6-luna',worker,decision,advisor,apply,lambda *args:None)
  return await c.run('original'),events
 async def test_overspend_still_integrates_and_counts(self):
  r,e=await self.simulate(['advisor'],patch(),1.2)
  self.assertEqual(r['terminal_reason'],'budget_exhausted_after_integration');self.assertAlmostEqual(r['cost_usd'],1.23)
  self.assertEqual(r['integrations'],1);self.assertEqual(sum(x[0]=='gate' for x in e),1)
  self.assertLess(next(i for i,x in enumerate(e) if x[0]=='apply'),next(i for i,x in enumerate(e) if x[:2]==('worker','worker-integration-0')))
  self.assertGreater(e[-1][3],0)
 async def test_preparation_once(self):
  r,e=await self.simulate(['prepare','prepare','deliver'],patch())
  self.assertEqual(r['preparations'],1);self.assertEqual(r['advisor_calls'],1);self.assertEqual(r['terminal_reason'],'deliver')
 async def test_malformed_patch_is_model_failure_not_infra(self):
  for bad in [None,[],['bad'],{'status':'patch'}]:
   r,e=await self.simulate(['advisor','deliver'],bad)
   self.assertEqual(r['integrations'],1);self.assertFalse(any(x[0]=='apply' for x in e))
   handoff=next(x[2] for x in e if x[:2]==('worker','worker-integration-0'))
   self.assertFalse(handoff['patch_receipt']['applied']);self.assertEqual(handoff['actual_file_paths'],[])
 async def test_deliver_requires_worker_implementation(self):
  r,e=await self.simulate(['deliver','deliver'],patch())
  self.assertTrue(any(x[:2]==('worker','worker-implementation-0') for x in e))
 def test_paths_cannot_escape(self):
  for path in ['/tmp/x','/app/../secret','/app/.git/config','relative.py']:
   p=patch();p['files'][0]['path']=path
   with self.assertRaises(ValueError):validate_patch(p)
if __name__=='__main__':unittest.main()
