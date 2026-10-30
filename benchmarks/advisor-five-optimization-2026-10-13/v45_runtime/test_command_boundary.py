import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from .command_boundary import strict_shell, prepare_component_directory
from v32_runtime.worker import bounded_worker


class BoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)

    def tearDown(self): self.tmp.cleanup()

    def execute(self, command):
        return subprocess.run(['/bin/bash','-c',strict_shell(command)],cwd=self.root,text=True,capture_output=True)

    def test_unhandled_failure_prevents_later_success_masking(self):
        r=self.execute('cat missing > output; touch should_not_exist')
        self.assertNotEqual(r.returncode,0)
        self.assertFalse((self.root/'should_not_exist').exists())

    def test_pipeline_failure_is_not_hidden_by_last_stage(self):
        r=self.execute('printf data | false | cat; touch should_not_exist')
        self.assertNotEqual(r.returncode,0)
        self.assertFalse((self.root/'should_not_exist').exists())

    def test_expected_nonfatal_condition_can_be_handled_explicitly(self):
        r=self.execute('if test -e optional; then cat optional; else printf absent; fi')
        self.assertEqual(r.returncode,0)
        self.assertEqual(r.stdout,'absent')

    def test_command_text_is_quoted_without_outer_expansion(self):
        r=self.execute("python3 - <<'PY'\nfrom pathlib import Path\nPath('literal').write_text('$(touch injected) `touch injected2`')\nPY")
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual((self.root/'literal').read_text(),'$(touch injected) `touch injected2`')
        self.assertFalse((self.root/'injected').exists())
        self.assertFalse((self.root/'injected2').exists())

    async def test_bounded_worker_retains_error_and_unexecuted_second_command(self):
        commands=[]
        async def native(*args):
            return {'status':'completed','usage':{'cost_usd':.01,'native_usage':dict(inputTokens=100,outputTokens=100,cacheReadTokens=0,cacheWriteTokens=0)},
                    'patch':{'status':'commands','commands':['cat missing > out; touch masked','touch second'],
                             'report':{'summary':'Intended checks','checks':[]}},'evidence':{'text':''}}
        async def execute(command):
            commands.append(command);r=self.execute(command)
            return SimpleNamespace(return_code=r.returncode,stdout=r.stdout,stderr=r.stderr)
        result=await bounded_worker(self.root/'worker-discovery','Synthetic component contract','low',.02,execute,native=native,
                                    execution_label='Daytona task workspace only; Bash -e -o pipefail')
        self.assertEqual(len(commands),1)
        self.assertEqual(result['usage']['cost_usd'],.01)
        self.assertNotEqual(result['evidence']['commands'][0]['exit_code'],0)
        history=json.loads(result['evidence']['text'])['protocol_history']
        self.assertEqual(history[0]['unexecuted_commands'],['touch second'])
        self.assertFalse((self.root/'second').exists())
        self.assertFalse((self.root/'masked').exists())

    async def test_setup_records_real_success_or_failure_without_model(self):
        for code in [0,1]:
            run=AsyncMock(return_value=SimpleNamespace(return_code=code,stdout='',stderr='' if code==0 else 'denied'))
            receipt=await prepare_component_directory(run)
            self.assertEqual(receipt['exit_code'],code)
            self.assertEqual(receipt['status'],'completed' if code==0 else 'failed')
            self.assertEqual(receipt['model_calls'],0)
            self.assertFalse(receipt['correctness_proven'])
            self.assertEqual(run.await_count,1)


if __name__=='__main__':unittest.main()
