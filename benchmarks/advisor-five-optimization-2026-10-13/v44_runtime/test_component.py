import hashlib
import json
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from .component_runner import run_component
from .component_evidence import summarize
from .coordinator import Coordinator


MODULE = '''import json
from pathlib import Path
def run(payload, output_dir):
    p=Path(output_dir)/'records.json'
    p.write_text(json.dumps(payload['records']))
    return {'files':[str(p)]}
'''


class ComponentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.module=self.root/'worker_component.py';self.module.write_text(MODULE)
        self.data=self.root/'data.json';self.data.write_text('{"records":[1,2]}')
        self.receipts=self.root/'receipts.jsonl'

    def tearDown(self): self.temp.cleanup()

    def invoke(self, purpose='original_task'):
        return run_component(self.module,self.data,self.root/'out',self.receipts,purpose,root=self.root)

    def test_actual_output_bytes_and_source_inputs_are_bound(self):
        r=self.invoke()
        self.assertEqual(r['status'],'completed')
        self.assertFalse(r['correctness_proven'])
        self.assertEqual(r['model_calls'],0)
        self.assertEqual(r['python_executable'],sys.executable)
        self.assertEqual(json.loads((self.root/'out/records.json').read_text()),[1,2])
        for field,file in [('module_sha256',self.module),('input_sha256',self.data)]:
            self.assertEqual(r[field],hashlib.sha256(file.read_bytes()).hexdigest())
        self.assertEqual(r['files'][0]['sha256'],hashlib.sha256((self.root/'out/records.json').read_bytes()).hexdigest())

    def test_synthetic_and_original_calls_remain_distinct(self):
        a=self.invoke('synthetic_check');b=self.invoke()
        self.assertNotEqual(a['invocation_id'],b['invocation_id'])
        rows=[json.loads(x) for x in self.receipts.read_text().splitlines()]
        self.assertEqual([x['purpose'] for x in rows],['synthetic_check','original_task'])

    def test_component_failure_keeps_failed_receipt_and_raises(self):
        self.data.write_text('{}')
        with self.assertRaises(KeyError):self.invoke()
        row=json.loads(self.receipts.read_text())
        self.assertEqual(row['status'],'failed')
        self.assertEqual(row['error_type'],'KeyError')
        self.assertNotIn('files',row)

    def test_changed_source_not_stale_bytecode(self):
        self.invoke()
        self.module.write_text(MODULE.replace("payload['records']","list(reversed(payload['records']))"))
        self.invoke()
        self.assertEqual(json.loads((self.root/'out/records.json').read_text()),[2,1])

    def test_output_cannot_be_input_or_outside_output_directory(self):
        for target in [str(self.data),'../data.json']:
            self.module.write_text('def run(payload, output_dir):\n    return {"files":'+repr([target])+'}\n')
            with self.assertRaises(ValueError):self.invoke()

    def test_receipt_cannot_replace_input_or_escape_root(self):
        with self.assertRaises(ValueError):
            run_component(self.module,self.data,self.root/'out',self.data,'original_task',root=self.root)
        with self.assertRaises(ValueError):
            run_component(self.module,self.data,self.root/'out',self.root/'../outside.json','original_task',root=self.root)
        self.assertEqual(json.loads(self.data.read_text()),{'records':[1,2]})

    def test_no_output_is_not_a_successful_invocation(self):
        self.module.write_text('def run(payload, output_dir):\n    return {"files": []}\n')
        with self.assertRaises(ValueError):self.invoke()

    def test_reuse_summary_excludes_synthetic_and_compares_final_bytes(self):
        agent=self.root/'agent';agent.mkdir()
        artifacts=self.root/'artifacts';(artifacts/'app').mkdir(parents=True)
        (artifacts/'app/output.json').write_bytes(b'final')
        (agent/'worker-component-worker-discovery-module.py').write_text(MODULE)
        row={'status':'completed','purpose':'original_task','invocation_id':'one',
             'module_sha256':hashlib.sha256(MODULE.encode()).hexdigest(),
             'files':[{'path':'/app/output.json','sha256':hashlib.sha256(b'final').hexdigest()}]}
        (agent/'worker-component-final-receipts.jsonl').write_text(json.dumps(dict(row,purpose='synthetic_check'))+'\n'+json.dumps(row)+'\n')
        result=summarize(agent,artifacts)
        self.assertEqual(result['original_task_runs'],1)
        self.assertTrue(result['runs'][0]['module_matches_captured_worker_source'])
        self.assertEqual(result['runs'][0]['matching_final_artifacts'],['/app/output.json'])
        (artifacts/'app/output.json').write_bytes(b'later edit')
        self.assertEqual(summarize(agent,artifacts)['runs'][0]['matching_final_artifacts'],[])


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_model_cost_and_full_task_handoff_preserved(self):
        from v33_runtime.test_focus import FocusTests
        with patch('v33_runtime.test_focus.Coordinator',Coordinator):
            result,calls,gates,emits=await FocusTests().run_fixture()
        self.assertAlmostEqual(result['target_ceiling_usd'],.36)
        self.assertAlmostEqual(result['cost_usd'],.104)
        self.assertEqual(calls[0]['original_task'],calls[1]['original_task'])
        self.assertEqual(len(calls),2)
        self.assertFalse(result['verifier_feedback'])


if __name__=='__main__':unittest.main()
