import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from .worker_evidence import project,publish,canonical
from .delegation import InTurnWorker
from .test_delegation import WORK


def fixture():
    commands=[{'command':'python task.py','exit_code':1,'stdout':'partial result','stderr':'actual failure','output_truncated':True}]
    report={'summary':'Planned work','unresolved_assumptions':['Unverified'],
            'actual_command_results':copy.deepcopy(commands),
            'protocol_history':[{'worker_report_before_execution':{'summary':'Intended check'},
                                 'actual_command_results':copy.deepcopy(commands),
                                 'unexecuted_commands':['python final.py']}]}
    return {'phase':'worker-in-turn-advisor-0','status':'preparation_observed',
            'usage':{'cost_usd':.031},'evidence':{'commands':commands,'command_count':1,
                                               'text':json.dumps(report)}}


class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    def test_repeated_logs_removed_actual_failure_and_skips_retained(self):
        result=fixture();before=copy.deepcopy(result)
        out=project(result,{'path':'/app/evidence.json','available':True})
        self.assertEqual(len(out['command_results']),1)
        self.assertEqual(out['command_results'][0]['exit_code'],1)
        self.assertEqual(out['command_results'][0]['stderr'],'actual failure')
        self.assertTrue(out['command_results'][0]['output_truncated'])
        self.assertEqual(len(out['unexecuted_commands']),1)
        self.assertNotIn('command',out['command_results'][0])
        self.assertFalse(out['correctness_proven']);self.assertEqual(result,before)

    def test_two_actual_identical_executions_are_preserved(self):
        result=fixture();c=result['evidence']['commands'][0]
        result['evidence']['commands']=[c,copy.deepcopy(c)];result['evidence']['command_count']=2
        result['evidence']['text']=json.dumps({'actual_command_results':[c,c]})
        out=project(result,{})
        self.assertEqual(len(out['command_results']),2)
        self.assertFalse(out['conflicting_command_representations'])

    def test_conflicting_representations_are_not_silently_merged(self):
        result=fixture();nested=json.loads(result['evidence']['text'])
        nested['actual_command_results'][0]['exit_code']=0
        result['evidence']['text']=json.dumps(nested)
        out=project(result,{})
        self.assertTrue(out['conflicting_command_representations'])
        self.assertEqual(out['command_results'][0]['exit_code'],1)
        self.assertEqual(out['alternate_command_sequences'][0]['results'][0]['exit_code'],0)

    async def test_uploaded_full_evidence_is_hash_verifiable_and_budget_unchanged(self):
        result=fixture();uploaded=[]
        async def upload(local,remote):uploaded.append((local.read_bytes(),remote))
        with tempfile.TemporaryDirectory() as tmp:
            result['tool_evidence']=await publish(result,Path(tmp),'/app/evidence.json',upload)
            receipt=json.loads((Path(tmp)/'worker-evidence-receipt.json').read_text())
        self.assertEqual(hashlib.sha256(uploaded[0][0]).hexdigest(),receipt['raw_evidence_file']['sha256'])
        self.assertEqual(json.loads(uploaded[0][0])['evidence'],result['evidence'])
        async def worker(*args):return result
        bridge=InTurnWorker(worker,lambda:.1,.04,.01,lambda *_:None)
        actual=await bridge.invoke('one',WORK)
        self.assertEqual(actual['evidence'],result['tool_evidence'])
        self.assertEqual(actual['actual_cost_usd'],.031)
        self.assertEqual(bridge.records[0]['result']['evidence'],result['evidence'])

    async def test_upload_failure_returns_original_evidence_not_unreadable_hash(self):
        result=fixture()
        async def upload(*args):raise ConnectionError('Fixture upload unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            out=await publish(result,Path(tmp),'/app/evidence.json',upload)
            receipt=json.loads((Path(tmp)/'worker-evidence-receipt.json').read_text())
        self.assertEqual(out,result['evidence'])
        self.assertTrue(receipt['fallback_to_full_evidence'])
        self.assertFalse(receipt['raw_evidence_file']['available'])


if __name__=='__main__':unittest.main()
