import copy
import json
import tempfile
import unittest
from pathlib import Path
from .progress import validate_progress, collect_progress
from .coordinator import Coordinator

REPORT = {'token':'token','phase':'advisor-0','status':'in_progress',
          'required_work':'Distinct input records are not yet independently checked',
          'observed_evidence':'Current output was compared only with the same intermediate representation',
          'priority_check':None}


class ProgressTests(unittest.IsolatedAsyncioTestCase):
    def test_phase_identity_size_and_unfinished_requirement(self):
        raw=json.dumps(REPORT).encode()
        self.assertEqual(validate_progress(raw,'token','advisor-0'),REPORT)
        for token,phase in [('other','advisor-0'),('token','advisor-1')]:
            with self.assertRaises(ValueError):validate_progress(raw,token,phase)
        with self.assertRaises(ValueError):validate_progress(b' '*12289,'token','advisor-0')
        with self.assertRaises(ValueError):validate_progress(json.dumps(dict(REPORT,required_work=' ')).encode(),'token','advisor-0')

    async def test_download_retains_self_report_without_success_claim(self):
        with tempfile.TemporaryDirectory() as d:
            async def download(source,target):target.write_text(json.dumps(REPORT))
            receipt=await collect_progress({'phase':'advisor-0','token':'token','path':'/app/progress'},Path(d)/'p',download)
            self.assertEqual(receipt['report'],REPORT)
            self.assertFalse(receipt['correctness_proven'])
            self.assertEqual(receipt['model_calls'],0)
            self.assertEqual(receipt['grader_calls'],0)

    async def test_unavailable_or_stale_is_never_used_as_success(self):
        with tempfile.TemporaryDirectory() as d:
            async def missing(source,target):raise FileNotFoundError()
            async def stale(source,target):target.write_text(json.dumps(dict(REPORT,phase='advisor-9')))
            for download in [missing,stale]:
                receipt=await collect_progress({'phase':'advisor-0','token':'token','path':'/app/progress'},Path(d)/'p',download)
                self.assertIsNone(receipt['report'])
                self.assertFalse(receipt['correctness_proven'])

    async def test_empty_final_still_forwards_progress_and_clears_stale_report(self):
        calls,gates=[],[]
        async def unused(*args):raise AssertionError('No extra model or finalizer call')
        async def expert(phase,payload):
            calls.append(copy.deepcopy(payload))
            result={'status':'interrupted','usage':{'cost_usd':.02},'evidence':{'text':''},'interrupted':'budget'}
            if len(calls)==1:result['expert_progress']=copy.deepcopy(REPORT)
            return result
        async def gate(phase,payload):
            gates.append(copy.deepcopy(payload))
            return {'status':'completed','usage':{'cost_usd':.001},'decision':{'action':'advisor'}}
        c=Coordinator(.5,'cursor/grok-4.7-low',unused,gate,expert,lambda *args:None,unused,unused)
        payload={'original_task':'Original task','contract':{'priority_check':None},'cost_target_usd':.1}
        await c.advisor('advisor-0',payload)
        await c.decision('decision-1',{'worker_evidence':{'text':''}})
        c.budget.advisor_calls=1
        await c.advisor('advisor-1',payload)
        await c.decision('decision-2',{'worker_evidence':{'text':''}})
        self.assertNotIn('expert_progress',calls[0])
        self.assertEqual(calls[1]['expert_progress'],REPORT)
        self.assertEqual(gates[0]['expert_progress'],REPORT)
        self.assertNotIn('expert_progress',gates[1])
        self.assertEqual(len(calls),2)
        self.assertEqual(len(gates),2)
        self.assertNotIn('expert_progress',payload)

    def test_context_delta_cannot_drop_new_progress(self):
        from v32_runtime.context_delta import resume_delta
        evidence={'text':'Prior native evidence'}
        payload={'worker_evidence':evidence,'expert_progress':REPORT,'contract':{'action':'advisor'}}
        memory={'thread_id':'t','turn_id':'u','phase':'advisor-0','compacted':False,'evidence':evidence}
        compact,receipt=resume_delta(payload,memory,'t')
        self.assertIsNotNone(receipt)
        self.assertEqual(compact['expert_progress'],REPORT)
