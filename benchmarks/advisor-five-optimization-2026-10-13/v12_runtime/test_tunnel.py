import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from .tunnel import TunnelSupervisor, guarded_turn


class Process:
    def __init__(self):
        self.returncode = None
        self.done = asyncio.Event()
    async def wait(self):
        await self.done.wait()
        return self.returncode
    def exit(self, code):
        self.returncode = code
        self.done.set()
    def terminate(self):
        self.exit(-15)
    def kill(self):
        self.exit(-9)


class TunnelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.processes, self.events = [], []
        async def spawn():
            p = Process()
            self.processes.append(p)
            return p
        async def ready(p):
            self.assertIsNone(p.returncode)
        self.supervisor = TunnelSupervisor(spawn, ready, self.events.append, max_restarts=1)
        await self.supervisor.start()
    async def asyncTearDown(self):
        await self.supervisor.close()
    async def test_live_process_is_never_restarted(self):
        for _ in range(3):
            await self.supervisor.ensure_ready()
            await asyncio.sleep(.01)
        self.assertEqual(len(self.processes), 1)
    async def test_confirmed_exit_recovers_without_command_replay(self):
        self.processes[0].exit(255)
        await asyncio.wait_for(self.supervisor.ensure_ready(), 2)
        self.assertEqual(len(self.processes), 2)
        self.assertIsNone(self.processes[1].returncode)
        self.assertEqual(self.events[1]['task_commands_replayed'], 0)
    async def test_exhaustion_prevents_future_model_calls(self):
        self.processes[0].exit(255)
        await asyncio.wait_for(self.supervisor.ensure_ready(), 2)
        self.processes[1].exit(255)
        await asyncio.wait_for(self.supervisor.failed.wait(), 2)
        calls = []
        async def model():
            calls.append(True)
        with self.assertRaises(RuntimeError):
            await guarded_turn(None, self.supervisor, model)
        self.assertEqual(calls, [])
    async def test_natural_model_completion_preserved(self):
        expected = {'usage': {'cost_usd': .125}, 'status': 'completed'}
        async def model():
            return expected
        self.assertEqual(await guarded_turn(None, self.supervisor, model), expected)
    async def test_active_turn_interrupt_retains_all_cost(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            (p / 'app-server.events.jsonl').write_text(json.dumps({
                'method': 'turn/started', 'params': {'threadId': 'thread', 'turn': {'id': 'turn'}}}) + '\n')
            interrupted = asyncio.Event()
            calls = []
            async def rpc(method, params):
                calls.append((method, params))
                interrupted.set()
            async def model():
                await interrupted.wait()
                return {'usage': {'cost_usd': .187}, 'status': 'interrupted'}
            client = SimpleNamespace(log_dir=p, rpc=rpc)
            run = asyncio.create_task(guarded_turn(client, self.supervisor, model))
            await asyncio.sleep(0)
            self.processes[0].exit(255)
            await self.supervisor.ensure_ready()
            self.processes[1].exit(255)
            result = await asyncio.wait_for(run, 2)
            self.assertEqual(result['usage']['cost_usd'], .187)
            self.assertEqual(result['transport_failure'], 'ssh_recovery_exhausted')
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], 'turn/interrupt')
            self.assertEqual(json.loads((p / 'phase-result.json').read_text()), result)
    async def test_pre_turn_failure_cancels_start_without_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            cancelled = asyncio.Event()
            async def model():
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            run = asyncio.create_task(guarded_turn(SimpleNamespace(log_dir=Path(folder)), self.supervisor, model))
            await asyncio.sleep(0)
            self.processes[0].exit(255)
            await self.supervisor.ensure_ready()
            self.processes[1].exit(255)
            with self.assertRaises(RuntimeError):
                await asyncio.wait_for(run, 2)
            self.assertTrue(cancelled.is_set())
    async def test_close_stops_owned_process(self):
        await self.supervisor.close()
        self.assertEqual(self.processes[0].returncode, -15)


if __name__ == '__main__':
    unittest.main()
