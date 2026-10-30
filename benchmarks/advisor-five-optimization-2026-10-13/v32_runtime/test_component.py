import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import jsonschema
from v11_runtime.test_worker import answer
from .worker import bounded_worker
from .coordinator import Coordinator
from .policy import PLAN_SCHEMA, preparation_images, INITIAL_WORK

CONTRACT={'work_kind':'component_implementation','objective':'Implement a small reusable input reader','input_paths':['/app/input'], 'requirement_excerpt':'Read the original input records.', 'expected_evidence':'An implemented function and actual round-trip check', 'specialist_boundary':'Interpretation of the records remains unresolved', 'component_interface':'read_records(path) returns a list of source records without interpreting their labels', 'excluded_work':['Inferring labels and generating the final answer'], 'artifact_role':'reusable_component'}

class ComponentContractTests(unittest.TestCase):
    def test_valid_contract_and_reject_whole_task_kind(self):
        jsonschema.validate(CONTRACT,PLAN_SCHEMA)
        bad=dict(CONTRACT,work_kind='bounded_implementation')
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(bad,PLAN_SCHEMA)
    def test_contract_requires_interface_and_exclusion(self):
        for key in ['component_interface','excluded_work','artifact_role']:
            bad=dict(CONTRACT);del bad[key]
            with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(bad,PLAN_SCHEMA)
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(dict(CONTRACT,excluded_work=[]),PLAN_SCHEMA)
    def test_initial_and_repair_image_scope_excludes_whole_problem(self):
        images=[{'data':'synthetic','mimeType':'image/png'}]
        for phase in ['worker-discovery','worker-preparation-0']:
            self.assertEqual(preparation_images(phase,images),[])
        self.assertEqual(preparation_images('worker-implementation-2',images),images)

class ComponentWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def scenario(self,reply):
        calls=[];commands=[]
        async def native(folder,prompt,effort):calls.append(prompt);return reply
        async def execute(command):
            commands.append(command)
            return SimpleNamespace(return_code=4 if command=='fails' else 0,stdout='Actual component check',stderr='actual exception' if command=='fails' else '')
        with tempfile.TemporaryDirectory() as tmp:
            result=await bounded_worker(Path(tmp)/'worker-discovery','One component interface','low',.02,execute,native)
        return result,calls,commands
    async def test_one_native_call_executes_component_and_check_with_real_evidence(self):
        r,c,cmd=await self.scenario(answer(commands=['implement','check']))
        self.assertEqual(len(c),1);self.assertEqual(cmd,['implement','check'])
        self.assertNotEqual(r['status'],'completed')
        e=json.loads(r['evidence']['text']);self.assertEqual(e['actual_command_results'][-1]['stdout'],'Actual component check')
        self.assertTrue(e['report_is_before_execution'])
    async def test_failed_component_preserves_skipped_check(self):
        r,c,cmd=await self.scenario(answer(commands=['fails','must-not-run']))
        self.assertEqual(cmd,['fails']);self.assertEqual(len(c),1)
        e=json.loads(r['evidence']['text']);self.assertEqual(e['protocol_history'][0]['unexecuted_commands'],['must-not-run'])
        self.assertEqual(e['actual_command_results'][0]['stderr'],'actual exception')
    async def test_excess_commands_are_not_executed(self):
        r,c,cmd=await self.scenario(answer(commands=['a','b','c']))
        self.assertFalse(cmd);self.assertEqual(len(c),1)
    async def test_overshoot_and_unknown_usage_never_zeroed(self):
        for cost in [.05,None]:
            r,c,cmd=await self.scenario(answer('needs_advisor',cost=cost))
            self.assertEqual(r['usage']['cost_usd'],cost);self.assertEqual(len(c),1)

class ComponentRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def scenario(self,actions):
        work=[];advice=[];gates=[];receipts={}
        async def planner(*args):return {'status':'completed','usage':{'cost_usd':.001},'work_order':CONTRACT}
        async def worker(phase,payload,policy,cap):
            work.append((phase,payload,policy,cap));return {'status':'preparation_observed','usage':{'cost_usd':.02},'evidence':{'text':'Component function checked; specialist inference remains'}}
        async def decision(phase,payload):
            gates.append(payload);return {'status':'completed','usage':{'cost_usd':.001},'decision':{'action':actions[len(gates)-1], 'issue':'Original uncovered requirement '+str(len(gates)), 'evidence':payload['worker_evidence']['text'],'paths':['/app/component.py'],'intervention':'Resolve the uncovered original requirement','acceptance':'Full original task'}}
        async def advisor(phase,payload):
            advice.append(payload);return {'status':'completed','usage':{'cost_usd':.02},'evidence':{'text':'{"status":"completed"}'}}
        async def finalizer(*args):return None
        c=Coordinator(.5,'cursor/grok-4.7-low',worker,decision,advisor,lambda k,v:receipts.update({k:v}),planner,finalizer)
        r=await c.run('Original full specialist assignment retained by reviewer and advisor')
        return r,work,advice,gates
    async def test_worker_gets_only_component_but_review_and_advisor_keep_full_assignment(self):
        r,w,a,g=await self.scenario(['advisor','deliver'])
        self.assertNotIn('original_task',w[0][1]);self.assertEqual(w[0][1]['work_order'],CONTRACT)
        self.assertEqual(w[0][2],INITIAL_WORK)
        self.assertIn('full specialist assignment',g[0]['original_task'])
        self.assertIn('full specialist assignment',a[0]['original_task'])
        self.assertEqual(r['terminal_reason'],'deliver')
    async def test_component_success_cannot_deliver_original_task(self):
        r,w,a,g=await self.scenario(['deliver','deliver'])
        self.assertEqual(g[0]['stage'],'discovery')
        self.assertEqual(len(a),1);self.assertEqual(len(w),1)
        self.assertEqual(r['terminal_reason'],'deliver')
    async def test_second_worker_repair_cannot_expand_or_loop(self):
        r,w,a,g=await self.scenario(['continue','continue','deliver'])
        self.assertEqual(len(w),2);self.assertEqual(len(a),1)
        self.assertEqual(w[1][1]['work_order'],CONTRACT)
        self.assertEqual(g[1]['stage'],'discovery')
        self.assertFalse(r['verifier_feedback'])

if __name__=='__main__':unittest.main()
