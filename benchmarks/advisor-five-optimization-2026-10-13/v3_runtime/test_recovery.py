import json,tempfile,unittest
from pathlib import Path
from v3_runtime.recovery import recover_public_text
from v3_runtime.policy import validate_patch
from hybrid_agent import parse_json
class Tests(unittest.TestCase):
 def test_unknown_advisor_cost_still_integrates_without_claiming_known_total(self):
  import asyncio
  from v3_runtime.coordinator import Coordinator
  phases=[]
  def result(cost):return {'usage':{'cost_usd':cost},'status':'completed','evidence':{'text':'public evidence'}}
  async def worker(phase,payload,policy,cap):phases.append(phase);return result(.01)
  async def decision(phase,payload):return dict(result(.01),decision={'action':'advisor','issue':'risk','evidence':'uncertain','paths':[],'intervention':'fix','acceptance':'check'})
  async def advisor(phase,payload):return dict(result(None),status='error',patch={})
  async def apply(p):raise AssertionError('Invalid patch must not be applied')
  c=Coordinator(1,'gpt-6-luna',worker,decision,advisor,apply,lambda *args:None)
  r=asyncio.run(c.run('original'))
  self.assertEqual(r['integrations'],1);self.assertIsNone(r['cost_usd']);self.assertFalse(r['usage_complete'])
  self.assertAlmostEqual(r['known_cost_lower_bound_usd'],.03);self.assertEqual(r['terminal_reason'],'budget_exhausted_after_integration')
 def run_case(self,native,events):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'events.jsonl';p.write_text('\n'.join(json.dumps(e) for e in events));return recover_public_text(native,p)
 def test_complete_public_patch_survives_terminal_transport_error(self):
  patch={'status':'patch','files':[{'path':'/app/f.py','content':'x = 1\n'}],'validation_commands':[],'requested_evidence':[],'handoff':'Execute checks'}
  text=json.dumps(patch);events=[{'type':'assistant','message':{'role':'assistant','content':[{'type':'text','text':text[i:i+3]}]}} for i in range(0,len(text),3)]
  recovered,origin=self.run_case({'status':'error','error':{'code':'connection_stalled'}},events)
  self.assertEqual(recovered,text);validate_patch(parse_json(recovered));self.assertEqual(origin,'public_assistant_stream_deltas')
 def test_reasoning_never_becomes_answer(self):
  text,_=self.run_case({'status':'error'},[{'type':'thinking','text':'secret reasoning'},{'type':'assistant','message':{'role':'assistant','content':[{'type':'reasoning','text':'private'}]}}]);self.assertEqual(text,'')
 def test_final_result_not_duplicated_with_stream(self):
  text,origin=self.run_case({'result':'complete'},[{'type':'assistant','message':{'role':'assistant','content':[{'type':'text','text':'complete'}]}}]);self.assertEqual(text,'complete');self.assertEqual(origin,'native_final_result')
 def test_truncated_stream_not_invented_into_valid_patch(self):
  text,_=self.run_case({'status':'error'},[{'type':'assistant','message':{'role':'assistant','content':[{'type':'text','text':'{"status":"patch","files":['}]}}])
  with self.assertRaises(Exception):validate_patch(parse_json(text))
if __name__=='__main__':unittest.main()
