import contextlib,io,json,shlex,subprocess,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from v7_runtime.metrics import aggregate_phases
from v7_runtime.remote import run_task_command

class MetricsTests(unittest.TestCase):
    def test_incomplete_phase_keeps_known_native_rounds(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);phase=root/'worker-discovery';phase.mkdir()
            (phase/'request.json').write_text(json.dumps({'model':'grok-4.7-low'}))
            (phase/'phase-result.json').write_text(json.dumps({'usage':{'cost_usd':None}}))
            for i,c in enumerate([.02,None]):
                p=phase/'rounds'/str(i);p.mkdir(parents=True)
                (p/'phase-result.json').write_text(json.dumps({'usage':{'cost_usd':c}}))
            total=aggregate_phases(root)
            self.assertFalse(total['complete']);self.assertIsNone(total['cost_usd'])
            self.assertEqual(total['known_cost_lower_bound_usd'],.02)

    def test_complete_phase_not_double_counted(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);phase=root/'worker-discovery';phase.mkdir()
            (phase/'request.json').write_text(json.dumps({'model':'grok-4.7-low'}))
            (phase/'phase-result.json').write_text(json.dumps({'usage':{'cost_usd':.02,'native_usage':{'inputTokens':100,'cacheReadTokens':20,'cacheWriteTokens':0,'outputTokens':10}}}))
            p=phase/'rounds'/'0';p.mkdir(parents=True)
            (p/'phase-result.json').write_text(json.dumps({'usage':{'cost_usd':.02}}))
            total=aggregate_phases(root)
            self.assertEqual(total['cost_usd'],.02);self.assertEqual(total['known_cost_lower_bound_usd'],.02)

class RemoteTests(unittest.IsolatedAsyncioTestCase):
    async def run_fake(self,command,timeout=False):
        class Agent:
            async def exec_as_agent(self,environment,command,**kwargs):
                assert shlex.split(command)[:2]==['python3','-c']
                script=shlex.split(command)[2]
                process=SimpleNamespace(pid=123,returncode=7)
                outputs=iter([subprocess.TimeoutExpired('task',300),('partial','diagnostic')]) if timeout else iter([('actual stdout','actual stderr')])
                def communicate(**kwargs):
                    value=next(outputs)
                    if isinstance(value,Exception):raise value
                    return value
                process.communicate=communicate
                with patch('subprocess.Popen',return_value=process) as spawn,patch('os.killpg') as kill:
                    output=io.StringIO()
                    with contextlib.redirect_stdout(output):exec(script,{})
                    self.original_command=spawn.call_args.args[0]
                    self.kill_count=kill.call_count
                return SimpleNamespace(return_code=0,stdout=output.getvalue())
        agent=Agent();result=await run_task_command(agent,object(),command)
        return result,agent

    async def test_command_is_exact_data_until_remote_execution(self):
        command='printf "$(not-a-host-command)"\n# quotes \' "'
        r,a=await self.run_fake(command)
        self.assertEqual(a.original_command,command);self.assertEqual(r.return_code,7)
        self.assertEqual(r.stdout,'actual stdout');self.assertEqual(a.kill_count,0)

    async def test_deadline_is_observed_command_failure(self):
        r,a=await self.run_fake('long task',timeout=True)
        self.assertEqual(r.return_code,124);self.assertTrue(r.deadline_exceeded)
        self.assertEqual(r.stdout,'partial');self.assertIn('deadline exceeded',r.stderr)
        self.assertEqual(a.kill_count,1)

if __name__=='__main__':unittest.main()
