import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from .handoff import collect_handoff, validate_manifest
from .coordinator import Coordinator
from .worker import bounded_worker


def phase(cost=.01, text='{}', **extra):
    return {'status':'completed','usage':{'cost_usd':cost},'evidence':{'text':text,'commands':[]},**extra}


def decision(action):
    return phase(.001, decision={'action':action,'issue':'Current requirement','evidence':'Actual evidence',
                 'paths':['/app/input.json'],'intervention':'Finish the original output','acceptance':'Check original requirement','priority_check':None})


def manifest():
    data=b'{"resolved_value":7}'
    c={'token':'token','phase':'advisor-0','manifest_path':'/app/.handoff.json'}
    m={'version':1,'token':'token','phase':'advisor-0','status':'ready',
       'inputs':[{'path':'/app/core.json','sha256':hashlib.sha256(data).hexdigest(),'role':'Expert-derived current data'}],
       'input_contract':'JSON object with resolved_value integer; preserve this value.',
       'worker_task':'Write the supplied resolved value as text.', 'required_outputs':['/app/result.txt'],
       'acceptance_checks':['Compare produced text to core.json resolved_value'],
       'specialist_evidence':['Current computation observed in task workspace'], 'unresolved_specialist_questions':[]}
    return c,m,data


class HandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_hashes_required_and_no_correctness_claim(self):
        c,m,data=manifest()
        async def download(remote, local):
            local.write_bytes(json.dumps(m).encode() if remote==c['manifest_path'] else data)
        with tempfile.TemporaryDirectory() as tmp:
            r=await collect_handoff(c,Path(tmp),download)
            self.assertEqual(r['status'],'available');self.assertFalse(r['correctness_proven'])
            self.assertEqual(r['model_calls'],0)
            data=b'changed'
            r=await collect_handoff(c,Path(tmp),download)
            self.assertEqual(r['status'],'invalid')

    def test_incomplete_stale_or_unsafe_manifest_is_rejected(self):
        c,m,_=manifest()
        changes=[('phase','advisor-1'),('token','stale'),('unresolved_specialist_questions',['unknown core']),
                 ('inputs',[]),('required_outputs',['/tmp/out']),('worker_task','')]
        for key,value in changes:
            with self.subTest(key=key):
                bad=copy.deepcopy(m);bad[key]=value
                with self.assertRaises(ValueError):validate_manifest(bad,c)
        bad=copy.deepcopy(m);bad['inputs'][0]['path']='/app/../secret'
        with self.assertRaises(ValueError):validate_manifest(bad,c)

    async def test_missing_input_is_unavailable_not_ready(self):
        c,m,_=manifest()
        async def download(remote, local):
            if remote==c['manifest_path']:local.write_text(json.dumps(m))
            else:raise FileNotFoundError(remote)
        with tempfile.TemporaryDirectory() as tmp:
            r=await collect_handoff(c,Path(tmp),download)
        self.assertEqual(r['status'],'unavailable')


class FlowTests(unittest.IsolatedAsyncioTestCase):
    def setup_flow(self, actions, experts, worker_results=None, fresh=None):
        c,m,_=manifest()
        handoff={'status':'available','phase':'advisor-0','contract':c,'manifest':m,'manifest_sha256':'hash','correctness_proven':False}
        self.handoff=handoff
        self.worker=AsyncMock(side_effect=worker_results or [phase(.01),phase(.02)])
        self.gate=AsyncMock(side_effect=[decision(x) for x in actions])
        self.expert=AsyncMock(side_effect=experts)
        self.finalizer=AsyncMock(return_value=None)
        self.revalidate=AsyncMock(return_value=fresh or handoff)
        self.events=[]
        self.c=Coordinator(1.,'cursor/grok-4.7-low',self.worker,self.gate,self.expert,
                           lambda *x:self.events.append(x),self.finalizer,self.revalidate)
        return self.c

    async def test_first_expert_uses_continuous_available_budget(self):
        c=self.setup_flow(['advisor','deliver'],[phase(.30,'{"status":"completed"}')])
        await c.run('Original requirements')
        payload=self.expert.await_args_list[0].args[1]
        self.assertAlmostEqual(payload['cost_target_usd'],.72-.001-.005)
        self.assertEqual(payload['review_checkpoint']['late_worker_reserve_usd'],0.)
        self.assertEqual(payload['original_task'],'Original requirements')

    async def test_valid_concrete_handoff_runs_worker_and_counts_every_phase(self):
        c=self.setup_flow(['advisor','continue','deliver'],[])
        self.expert.side_effect=[phase(.30,'{"status":"needs_advisor"}',late_handoff=self.handoff)]
        result=await c.run('Original requirements')
        self.assertEqual(result['terminal_reason'],'deliver')
        self.assertEqual(result['integrations'],1);self.assertEqual(result['advisor_calls'],1)
        self.assertAlmostEqual(result['cost_usd'],.313)
        call=self.worker.await_args_list[0]
        self.assertEqual(call.args[1]['verified_late_handoff'],self.handoff['manifest'])
        self.assertEqual(call.args[1]['original_task'],'Original requirements')
        self.assertEqual(self.revalidate.await_count,1)
        self.assertLessEqual(call.args[3],.08)

    async def test_prose_or_changed_files_stay_with_expert(self):
        for stale in [False,True]:
            c=self.setup_flow(['advisor','continue','deliver'],[],fresh={'status':'invalid'})
            first=phase(.30,'{"status":"needs_advisor"}')
            if stale:first['late_handoff']=self.handoff
            self.expert.side_effect=[first,phase(.10,'{"status":"completed"}')]
            result=await c.run('Original requirements')
            self.assertEqual(result['advisor_calls'],2);self.assertEqual(result['integrations'],0)
            self.assertEqual(self.worker.await_count,0)
            # Unused worker reservation is released for the second expert call.
            request=self.expert.await_args_list[1].args[1]
            self.assertEqual(request['review_checkpoint']['late_worker_reserve_usd'],0.)

    async def test_expert_can_finish_without_worker_rewrite(self):
        c=self.setup_flow(['advisor','deliver'],[phase(.30,'{"status":"completed"}')])
        r=await c.run('Original requirements')
        self.assertEqual(r['terminal_reason'],'deliver');self.assertEqual(self.worker.await_count,0)
        self.assertEqual(r['integrations'],0)

    async def test_failed_integration_cannot_be_delivered(self):
        failed=phase(.02);failed['evidence']['commands']=[{'exit_code':1,'stderr':'actual failure'}]
        c=self.setup_flow(['advisor','continue','deliver','deliver'],[],[failed])
        self.expert.side_effect=[phase(.30,'{"status":"needs_advisor"}',late_handoff=self.handoff),phase(.10,'{"status":"completed"}')]
        r=await c.run('Original requirements')
        self.assertEqual(r['advisor_calls'],2)
        self.assertEqual(r['terminal_reason'],'deliver')
        self.assertIn('actual failure',str(self.expert.await_args_list[1].args[1]['worker_evidence']))

    async def test_no_third_advisor_and_no_worker_for_missing_handoff(self):
        c=self.setup_flow(['advisor','advisor','continue'],[phase(.15),phase(.15)])
        r=await c.run('Original requirements')
        self.assertEqual(r['terminal_reason'],'advisor_limit_or_budget')
        self.assertEqual(r['advisor_calls'],2);self.assertEqual(self.worker.await_count,0)

    async def test_overspend_is_charged_and_no_extra_model_is_started(self):
        c=self.setup_flow(['advisor'],[phase(.80)])
        r=await c.run('Original requirements')
        self.assertAlmostEqual(r['cost_usd'],.801)
        self.assertEqual(r['terminal_reason'],'budget_exhausted')
        self.assertEqual(self.gate.await_count,1)

    async def test_unknown_usage_stops_without_claiming_zero_cost(self):
        c=self.setup_flow(['prepare'],[],[phase(None)])
        r=await c.run('Original requirements')
        self.assertIsNone(r['cost_usd']);self.assertFalse(r['usage_complete'])
        self.assertEqual(self.expert.await_count,0)

    async def test_late_cursor_worker_one_call_retains_skipped_commands(self):
        native=AsyncMock(return_value={'status':'completed','usage':{'cost_usd':.03,'native_usage':{'inputTokens':1,'outputTokens':1,'cacheReadTokens':0,'cacheWriteTokens':0}},
             'patch':{'status':'commands','commands':['first','second'],'report':{}},'evidence':{'text':''}})
        execute=AsyncMock(return_value=SimpleNamespace(return_code=1,stdout='',stderr='failed'))
        with tempfile.TemporaryDirectory() as tmp:
            r=await bounded_worker(Path(tmp)/'worker-integration-1','work','low',.02,execute,native)
        self.assertEqual(native.await_count,1);self.assertEqual(execute.await_count,1)
        self.assertEqual(r['usage']['cost_usd'],.03)
        self.assertEqual(json.loads(r['evidence']['text'])['protocol_history'][0]['unexecuted_commands'],['second'])


if __name__=='__main__':unittest.main()
