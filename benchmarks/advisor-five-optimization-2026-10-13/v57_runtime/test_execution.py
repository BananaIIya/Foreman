import asyncio
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from .execution import observe_command
from .worker_batch import execute_batch
from .worker_evidence import project
from .delegation import InTurnWorker
from . import test_delegation as fixtures

class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_observed_process_duration_and_error_are_retained(self):
        async def run(_):
            p=await asyncio.create_subprocess_exec(sys.executable,'-c',
                'import time,sys;time.sleep(.03);print("observed");sys.stderr.write("actual failure");sys.exit(4)',
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            out,err=await p.communicate()
            return SimpleNamespace(return_code=p.returncode,stdout=out.decode(),stderr=err.decode())
        row=await observe_command('fixture',run)
        self.assertEqual(row['exit_code'],4);self.assertEqual(row['stderr'],'actual failure')
        self.assertGreaterEqual(row['elapsed_wall_seconds'],.03)
        self.assertIsNone(row['deadline_exceeded']) # Missing evidence is not false.

    async def test_deadline_skips_remaining_batch_and_projection_preserves_timing(self):
        run=AsyncMock(return_value=SimpleNamespace(return_code=124,stdout='',stderr='deadline',deadline_exceeded=True))
        result={'status':'completed','usage':{'cost_usd':.03},
                'evidence':{'text':json.dumps({'status':'commands','commands':['slow','never-run'],'report':'Intended work'})}}
        observed=await execute_batch(result,run)
        self.assertEqual(run.await_count,1);self.assertEqual(observed['usage']['cost_usd'],.03)
        out=project(observed,{})
        row=out['command_results'][0]
        self.assertTrue(row['deadline_exceeded']);self.assertEqual(row['exit_code'],124)
        self.assertGreaterEqual(row['elapsed_wall_seconds'],0)
        self.assertEqual(len(out['unexecuted_commands']),1);self.assertFalse(out['correctness_proven'])

    async def test_transport_failure_does_not_fabricate_an_execution_observation(self):
        run=AsyncMock(side_effect=ConnectionError('fixture transport'))
        with self.assertRaises(ConnectionError):await observe_command('never-confirmed',run)

    async def test_missing_execution_plan_does_not_start_a_paid_worker(self):
        run=AsyncMock()
        bridge=InTurnWorker(run,lambda:.1,.04,.01,lambda *_:None)
        work=dict(fixtures.WORK);work.pop('execution_plan')
        result=await bridge.invoke('missing-plan',work)
        self.assertEqual(result['status'],'invalid_work_order');run.assert_not_awaited()
        self.assertEqual(bridge.calls,{})

if __name__=='__main__':unittest.main()
